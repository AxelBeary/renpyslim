"""清理与检测：打包前垃圾清理、废资源检测、重复文件检测。

全部遵循保守原则：
- 垃圾清理只删"定义上可再生"的东西（缓存/日志/临时字节码）；
- 废资源只报告，勾选后也只是移入隔离区，绝不直接删除；
- 图片永不标记为废资源（Ren'Py 8 会按文件名自动定义图片，
  字面引用搜索会漏掉这类用法）。
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from .models import AssetInfo, AssetKind

# 任意位置都安全删除的文件（可再生或与发布无关）
JUNK_FILES = {"errors.txt", "log.txt", "traceback.txt", "thumbs.db",
              "desktop.ini", ".ds_store"}
JUNK_EXTS = {".rpyb"}
# 审核修复（中-8）：saves/cache 只删"已知安全位置"——成品根平级与
# game/ 下；任意层级的同名目录可能是第三方游戏自建的必需数据
# （如 game/cache 存必需资源），曾无差别整删导致交付产物损坏
_SAFE_JUNK_DIR_RELS = ("saves", "cache", "game/saves", "game/cache")


def _is_rtools_tmp(name: str) -> bool:
    """本工具残留临时文件：名字含 .rtools. 且带 .tmp（审核修复
    高-3 后 tmp 名带随机后缀，不再能按固定后缀匹配）。"""
    return ".rtools." in name and ".tmp" in name


def clean_junk(root: str) -> dict:
    """删除目录树里的可再生垃圾，返回 {删除字节数, 删除项列表}。"""
    root_p = Path(root)
    freed = 0
    removed: list[str] = []

    def dir_size(p: Path) -> int:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

    # 目录垃圾：限已知安全位置（审核修复 中-8）
    for rel in _SAFE_JUNK_DIR_RELS:
        p = root_p / rel
        if not p.is_dir():
            continue
        try:
            freed += dir_size(p)
            shutil.rmtree(p, ignore_errors=True)
            removed.append(rel + "/")
        except OSError:
            continue

    # 文件垃圾：任意位置可删（errors.txt 等属 by-design）
    for p in sorted(root_p.rglob("*"), reverse=True):
        try:
            if p.is_file() and (p.name.lower() in JUNK_FILES
                                  or p.suffix.lower() in JUNK_EXTS
                                  or _is_rtools_tmp(p.name)):
                freed += p.stat().st_size
                p.unlink()
                removed.append(p.relative_to(root_p).as_posix())
        except OSError:
            continue
    return {"freed_bytes": freed, "removed": removed}


def find_unused_assets(assets: list[AssetInfo], ref_index) -> list[str]:
    """找出脚本里完全找不到字面引用的资源（相对路径列表）。

    只对音频/视频/字体生效：这类资源必须写明确路径才能用。
    图片一律不标记：Ren'Py 8 会按文件名自动生成图片定义，
    "没有字面引用"不等于"没有被用到"。
    """
    unused = []
    for a in assets:
        if a.kind not in (AssetKind.AUDIO, AssetKind.VIDEO, AssetKind.FONT):
            continue
        if not ref_index.find(a.rel):
            unused.append(a.rel)
    return sorted(unused)


def quarantine_files(root: str, rels: list[str]) -> list[str]:
    """把文件移入 <root>/_rtools_quarantine 隔离区，返回实际移动的路径。"""
    root_p = Path(root)
    qdir = root_p / "_rtools_quarantine"
    moved = []
    for rel in rels:
        src = root_p / rel
        if not src.exists():
            continue
        dst = qdir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            src.rename(dst)
            moved.append(rel)
        except OSError:
            continue
    return moved


def find_duplicates(assets: list[AssetInfo],
                    max_size_mb: int = 50) -> list[dict]:
    """按内容指纹找重复资源：先按体积粗筛，再算 MD5 精确分组。

    只返回确实重复的组：{hash, size, files:[相对路径...]}。
    """
    by_size: dict[int, list[AssetInfo]] = {}
    for a in assets:
        if a.size and a.size <= max_size_mb * 1048576:
            by_size.setdefault(a.size, []).append(a)

    by_hash: dict[str, list[AssetInfo]] = {}
    for group in by_size.values():
        if len(group) < 2:
            continue
        for a in group:
            try:
                h = hashlib.md5(Path(a.path).read_bytes()).hexdigest()
            except OSError:
                continue
            by_hash.setdefault(h, []).append(a)

    return [
        {"hash": h, "size": group[0].size,
         "files": sorted(a.rel for a in group)}
        for h, group in by_hash.items() if len(group) > 1
    ]
