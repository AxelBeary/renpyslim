"""备份与工作副本：所有破坏性操作前的安全网。"""
from __future__ import annotations

import os
import shutil
import time
import uuid
import zipfile
from pathlib import Path

# 复制工作副本时跳过的目录（可再生、体积大、与优化无关）
_COPY_SKIP = {"saves", "cache", "__pycache__", ".git", "tmp", "log"}

# 备份时只跳纯垃圾：存档（saves）必须进备份——它不可再生，
# 一旦后续操作出事，备份是唯一的救命稻草。（审核修复：曾误用 _COPY_SKIP
# 导致 in_place 备份里没有存档，清理又把存档删了，两头落空）
_BACKUP_SKIP = {"__pycache__", ".git"}


def make_working_copy(project_dir: str, dest_root: str) -> str:
    """把工程复制到工作目录，返回副本路径。原件保持不动。"""
    src = Path(project_dir)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = Path(dest_root) / f"{src.name}-working-{stamp}"

    def ignore(_dir, names):
        return [n for n in names if n in _COPY_SKIP]

    shutil.copytree(src, dest, ignore=ignore, dirs_exist_ok=False)
    return str(dest)


def make_backup_zip(target_dir: str, dest_zip: str) -> str:
    """把目录整体压成 zip 备份（直接修改原件前的强制备份）。

    审核修复（中-9）：先写 tmp 再原子落位——备份是 in_place 模式
    唯一的救命稻草，旧写法中途失败会留下"能打开但缺文件"的
    合法残缺备份，误导用户以为备份完好。
    """
    target = Path(target_dir)
    out = Path(dest_zip)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=6) as zf:
            for p in target.rglob("*"):
                if p.is_file():
                    if any(part in _BACKUP_SKIP
                           for part in p.relative_to(target).parts):
                        continue
                    zf.write(p, p.relative_to(target.parent))
        os.replace(tmp, out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return str(out)
