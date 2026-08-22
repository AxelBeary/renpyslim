"""增量缓存（BACKLOG B6）：按内容哈希记住已处理的文件，重跑秒级跳过。

设计：
- 缓存键 = 源文件 SHA-256 + 动作描述（档位/参数），参数变了自动失效
- 缓存体 = 优化后的文件字节，存放在用户目录 ~/.renpyslim/cache
- 命中流程：流水线先查缓存 → 命中直接把缓存内容写到目标位置，
  跳过压缩/转码；未命中照常处理，成功后入库
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CACHE_DIR = Path.home() / ".renpyslim" / "cache"
# 超过该体积的文件不做哈希缓存（哈希+复制本身比处理还贵时没意义）
MAX_CACHEABLE_MB = 80
# 审核修复（中-23）：缓存目录只写不清会无限膨胀，设容量上限，
# 超阈按最旧先删（淘汰检查限频，避免每次入库都扫全盘）
MAX_CACHE_BYTES = 2 * 1024 ** 3
_PRUNE_MIN_INTERVAL = 300.0
_last_prune = [0.0]


def _hash_file(path: str) -> Optional[str]:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


hash_file = _hash_file   # 对外别名：流水线在处理前先算好哈希，
# 避免原地替换后拿不到原件哈希


def _entry_path(file_hash: str, action_key: str) -> Path:
    combo = hashlib.sha256(f"{file_hash}|{action_key}".encode()).hexdigest()
    return CACHE_DIR / combo[:2] / combo


def lookup_hash(file_hash: str, action_key: str) -> Optional[str]:
    entry = _entry_path(file_hash, action_key)
    if not entry.exists():
        return None
    # 体积守卫下沉（审核修复）：超限条目按未命中处理，
    # 兼顾绕过 lookup()/store() 直调裸函数的调用方
    try:
        if entry.stat().st_size > MAX_CACHEABLE_MB * 1048576:
            logger.debug("缓存条目超限（> %dMB），按未命中处理：%s",
                         MAX_CACHEABLE_MB, str(entry))
            return None
    except OSError:
        return None
    return str(entry)


def store_hash(file_hash: str, action_key: str, optimized: str) -> None:
    # 体积守卫下沉（审核修复）：超限产物跳过入库，不抛错只记 DEBUG，
    # 不再依赖上层 lookup()/store() 的检查（直调裸函数也安全）
    try:
        if Path(optimized).stat().st_size > MAX_CACHEABLE_MB * 1048576:
            logger.debug("产物超限（> %dMB），跳过缓存入库：%s",
                         MAX_CACHEABLE_MB, optimized)
            return
    except OSError:
        return
    try:
        entry = _entry_path(file_hash, action_key)
        entry.parent.mkdir(parents=True, exist_ok=True)
        # 原子写入：tmp 名带随机后缀（审核修复）——并发写同一条目时
        # 各用各的 tmp，避免一方 copyfile 中途被另一方 replace 掉
        tmp = entry.with_name(f"{entry.name}.{uuid.uuid4().hex}.tmp")
        shutil.copyfile(optimized, tmp)
        os.replace(tmp, entry)
        _prune_if_needed()
    except OSError:
        pass


def store_self(optimized: str, action_key: str) -> None:
    """登记"已处理"自映射：产物自身哈希 -> 产物自身（审核修复 中-10）。

    in_place 反复运行时，上一轮的产物成为本轮的输入；没有这条
    自映射，JPG/WebP 等有损重编码会逐轮叠加（代际累积退化）。
    """
    try:
        if Path(optimized).stat().st_size > MAX_CACHEABLE_MB * 1048576:
            logger.debug("产物超限（> %dMB），跳过自映射入库：%s",
                         MAX_CACHEABLE_MB, optimized)
            return
    except OSError:
        return
    h = _hash_file(optimized)
    if h:
        store_hash(h, action_key, optimized)


def _prune_if_needed() -> None:
    """缓存总体积超上限时，从最旧的文件开始淘汰到 90% 以下。"""
    import time
    now = time.time()
    if now - _last_prune[0] < _PRUNE_MIN_INTERVAL:
        return
    _last_prune[0] = now
    try:
        files = []
        for f in CACHE_DIR.rglob("*"):
            if f.is_file():
                try:
                    files.append((f, f.stat()))
                except OSError:
                    continue
    except OSError:
        return
    total = sum(st.st_size for _, st in files)
    if total <= MAX_CACHE_BYTES:
        return
    files.sort(key=lambda t: t[1].st_mtime)   # 最旧优先
    for f, st in files:
        if total <= MAX_CACHE_BYTES * 9 // 10:
            break
        try:
            f.unlink()
            total -= st.st_size
        except OSError:
            continue


def lookup(src: str, action_key: str) -> Optional[str]:
    """查缓存：命中返回缓存文件路径，否则 None。"""
    try:
        if Path(src).stat().st_size > MAX_CACHEABLE_MB * 1048576:
            return None
    except OSError:
        return None
    file_hash = _hash_file(src)
    if not file_hash:
        return None
    return lookup_hash(file_hash, action_key)


def store(src: str, action_key: str, optimized: str) -> None:
    """把优化结果入库（失败静默，缓存只是加速不是功能）。"""
    try:
        if Path(src).stat().st_size > MAX_CACHEABLE_MB * 1048576:
            return
    except OSError:
        return
    file_hash = _hash_file(src)
    if file_hash:
        store_hash(file_hash, action_key, optimized)


def apply_cached(cached_path: str, dst: str) -> bool:
    """把缓存内容复制到目标位置。

    审核修复（中-4）：先写随机 tmp 再原子替换——直写目标时
    IO 故障会把待优化文件本身写成半截。
    """
    tmp = None
    try:
        dst_p = Path(dst)
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst_p.with_name(f"{dst_p.name}.{uuid.uuid4().hex}.tmp")
        shutil.copyfile(cached_path, tmp)
        os.replace(tmp, dst_p)
        return True
    except OSError:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        return False
