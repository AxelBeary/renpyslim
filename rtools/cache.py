"""增量缓存（BACKLOG B6）：按内容哈希记住已处理的文件，重跑秒级跳过。

设计：
- 缓存键 = 源文件 SHA-256 + 动作描述（档位/参数），参数变了自动失效
- 缓存体 = 优化后的文件字节，存放在用户目录 ~/.renpyslim/cache
- 命中流程：流水线先查缓存 → 命中直接把缓存内容写到目标位置，
  跳过压缩/转码；未命中照常处理，成功后入库
"""
from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

CACHE_DIR = Path.home() / ".renpyslim" / "cache"
# 超过该体积的文件不做哈希缓存（哈希+复制本身比处理还贵时没意义）
MAX_CACHEABLE_MB = 80


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
    return str(entry) if entry.exists() else None


def store_hash(file_hash: str, action_key: str, optimized: str) -> None:
    try:
        entry = _entry_path(file_hash, action_key)
        entry.parent.mkdir(parents=True, exist_ok=True)
        # 原子写入：tmp 名带随机后缀（审核修复）——并发写同一条目时
        # 各用各的 tmp，避免一方 copyfile 中途被另一方 replace 掉
        tmp = entry.with_name(f"{entry.name}.{uuid.uuid4().hex}.tmp")
        shutil.copyfile(optimized, tmp)
        os.replace(tmp, entry)
    except OSError:
        pass


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
    """把缓存内容复制到目标位置。"""
    try:
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cached_path, dst)
        return True
    except OSError:
        return False
