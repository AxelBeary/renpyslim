"""备份与工作副本：所有破坏性操作前的安全网。"""
from __future__ import annotations

import logging
import math
import os
import shutil
import time
import uuid
import zipfile
from pathlib import Path

from .archives import walk_tree
from .utils import fmt_size

logger = logging.getLogger(__name__)

# 复制工作副本时跳过的目录（可再生、体积大、与优化无关）
_COPY_SKIP = {"saves", "cache", "__pycache__", ".git", "tmp", "log"}

# 备份时只跳纯垃圾：存档（saves）必须进备份——它不可再生，
# 一旦后续操作出事，备份是唯一的救命稻草。（审核修复：曾误用 _COPY_SKIP
# 导致 in_place 备份里没有存档，清理又把存档删了，两头落空）
_BACKUP_SKIP = {"__pycache__", ".git"}


class BackupError(Exception):
    """备份/工作副本无法安全开始（如磁盘空间不足），消息可直接展示给用户。"""


def _tree_size(root: Path, skip: set[str]) -> int:
    """统计目录内受管文件的总字节数，跳过 skip 中同名的目录。"""
    total = 0
    if not root.is_dir():
        return 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
    return total


def _existing_ancestor(path: Path) -> Path:
    """目标可能尚不存在（如新的工作目录），向上找到最近的已存在祖先。"""
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            break
        current = parent
    return current


def ensure_free_space(dest: str | Path, required_bytes: int,
                      context: str = "写入") -> None:
    """确认目标磁盘能放下 required_bytes（另加 10% 余量）。

    空间不足时抛 BackupError，文案给出需要多少、可用多少、下一步怎么办。
    无法读取磁盘信息时不阻塞操作，只记一条警告。
    """
    required = math.ceil(required_bytes * 11 / 10)
    target = _existing_ancestor(Path(dest))
    try:
        free = shutil.disk_usage(target).free
    except OSError as exc:
        logger.warning("无法检查目标磁盘剩余空间（%s），跳过预检：%s",
                       target, exc)
        return
    if free < required:
        raise BackupError(
            f"磁盘空间不足（{context}）：预计需要约 {fmt_size(required)}"
            f"（含 10% 余量），目标位置 {target} 所在磁盘仅可用 "
            f"{fmt_size(free)}。请清理该磁盘，或用 --work-root / --output "
            "指定空间充足的磁盘后重试。"
        )


def make_working_copy(project_dir: str, dest_root: str) -> str:
    """把工程复制到工作目录，返回副本路径。原件保持不动。"""
    src = Path(project_dir)
    src_real = src.resolve()
    dest_real = Path(dest_root).resolve()
    if dest_real != src_real and src_real in dest_real.parents:
        # 工作目录落在工程内部时，copytree 会把工作副本再复制进自己，
        # 无限递归直到写满磁盘。
        raise BackupError(
            f"工作目录不能放在输入工程内部：{dest_root} 位于 {project_dir} 里。"
            "请改用工程外的目录（如 --work-root 指定别的路径）。")
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{uuid.uuid4().hex[:8]}"
    dest = Path(dest_root) / f"{src.name}-working-{stamp}"

    ensure_free_space(dest, _tree_size(src, _COPY_SKIP),
                      f"复制工作副本 {src.name}")

    skipped_links: list[str] = []

    def ignore(_dir, names):
        keep = []
        for name in names:
            if name in _COPY_SKIP:
                continue
            full = Path(_dir) / name
            # 符号链接 / 目录联接一律不复制：copytree 默认会跟随，
            # 把工程外的数据搬进工作副本（体积预检也没算它们），
            # 最终可能被打进交付包。扫描同样不跟链接，此处口径对齐。
            try:
                if full.is_symlink() or _is_junction(full):
                    skipped_links.append(str(full))
                    continue
            except OSError:
                continue
            keep.append(name)
        return [n for n in names if n not in keep]

    shutil.copytree(src, dest, ignore=ignore, dirs_exist_ok=False)
    if skipped_links:
        logger.warning("工作副本跳过 %d 个符号链接/联接（不跟随、不复制）：%s",
                       len(skipped_links), skipped_links[:5])
    return str(dest)


def _is_junction(path: Path) -> bool:
    """Windows 目录联接（junction）；其他平台恒为 False。"""
    checker = getattr(os.path, "isjunction", None)
    if checker is None:
        return False
    try:
        return bool(checker(path))
    except OSError:
        return False


def make_backup_zip(target_dir: str, dest_zip: str) -> str:
    """把目录整体压成 zip 备份（直接修改原件前的强制备份）。

    审核修复（中-9）：先写 tmp 再原子落位——备份是 in_place 模式
    唯一的救命稻草，旧写法中途失败会留下"能打开但缺文件"的
    合法残缺备份，误导用户以为备份完好。

    安全修复（2026-11-28 评审阻断 1）：遍历改走 archives.walk_tree，
    与交付打包共用同一份实现。旧版是 target.rglob("*") 配 p.is_file()，
    而 junction 是**目录**——is_file() 恒为 False，链接判定永远轮不到它，
    rglob 直接穿进联接里，把工程外的文件当普通文件写进备份包。
    """
    target = Path(target_dir)
    out = Path(dest_zip)
    out.parent.mkdir(parents=True, exist_ok=True)
    ensure_free_space(out, _tree_size(target, _BACKUP_SKIP),
                      f"备份 {target.name}")
    tmp = out.with_name(f"{out.name}.{uuid.uuid4().hex}.tmp")

    def add_file(path: Path, rel: Path) -> None:
        # rel 已带根目录名（walk_tree 的口径），解压出来是一个完整工程目录
        zf.write(str(path), rel.as_posix())

    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=6) as zf:
            walk_tree(target, add_file, skip_names=_BACKUP_SKIP)
        os.replace(tmp, out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return str(out)
