"""压缩包支持：zip / 7z / RAR 的识别、解压与重新打包。

- zip：Python 标准库直接搞定，支持密码
- 7z：纯 Python 库 py7zr，支持密码
- RAR：格式不开源，必须借助本机 7-Zip（7z.exe）；找不到时给出明确指引
重新打包统一输出 zip（兼容性最好）。
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Optional

from .procutil import run_quiet

ARCHIVE_EXTS = {".zip", ".7z", ".rar"}


class ArchiveError(Exception):
    pass


def is_archive(path: str) -> bool:
    return Path(path).suffix.lower() in ARCHIVE_EXTS


def find_7zip() -> Optional[str]:
    """找本机 7-Zip（用于解 RAR）。"""
    exe = shutil.which("7z")
    if exe:
        return exe
    for guess in (r"C:\Program Files\7-Zip\7z.exe",
                  r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if Path(guess).exists():
            return guess
    return None


def _zip_is_encrypted(path: str) -> bool:
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.flag_bits & 0x1:
                return True
    return False


def extract_archive(src: str, dest_dir: str,
                    password: Optional[str] = None) -> str:
    """解压压缩包到 dest_dir，返回解压根目录。

    密码错误、格式损坏、缺 7-Zip 等情况都抛 ArchiveError，
    错误信息是给最终用户看的人话。
    """
    src_p = Path(src)
    dest_p = Path(dest_dir)
    dest_p.mkdir(parents=True, exist_ok=True)
    ext = src_p.suffix.lower()
    pwd = password.encode("utf-8") if password else None

    try:
        if ext == ".zip":
            if _zip_is_encrypted(src) and not pwd:
                raise ArchiveError("这个压缩包有密码，请在高级选项里填写密码后重试。")
            try:
                with zipfile.ZipFile(src) as zf:
                    zf.extractall(dest_p, pwd=pwd)
            except RuntimeError as e:
                if "password" in str(e).lower() or "Bad password" in str(e):
                    raise ArchiveError("密码不对，解压失败。请检查密码后重试。")
                raise ArchiveError(f"压缩包损坏或格式异常：{e}")
        elif ext == ".7z":
            import py7zr
            try:
                with py7zr.SevenZipFile(src, mode="r", password=password) as z7:
                    if z7.needs_password() and not password:
                        raise ArchiveError("这个 7z 压缩包有密码，请在高级选项里填写密码后重试。")
                    z7.extractall(dest_p)
            except py7zr.PasswordRequired as exc:
                raise ArchiveError("这个 7z 压缩包有密码，请在高级选项里填写密码后重试。") from exc
            except ArchiveError:
                raise
            except Exception as e:
                if password and ("password" in str(e).lower() or "crc" in str(e).lower()):
                    raise ArchiveError("密码不对，解压失败。请检查密码后重试。")
                raise ArchiveError(f"7z 解压失败：{e}")
        elif ext == ".rar":
            exe7z = find_7zip()
            if not exe7z:
                raise ArchiveError(
                    "RAR 格式不开源，需要电脑上安装免费的 7-Zip 才能解压。"
                    "下载地址：https://www.7-zip.org/（装完重新拖入即可；"
                    "也可以先用别的工具解压成文件夹，再选文件夹处理）。")
            cmd = [exe7z, "x", "-y", f"-o{dest_p}", str(src_p)]
            if password:
                cmd.insert(2, f"-p{password}")
            proc = run_quiet(cmd, capture_output=True, timeout=7200)
            if proc.returncode != 0:
                err = proc.stderr.decode("utf-8", "replace")[-500:]
                raise ArchiveError(f"RAR 解压失败（密码错误或文件损坏）：{err}")
        else:
            raise ArchiveError(f"不支持的压缩包类型：{ext}")
    except ArchiveError:
        raise
    except Exception as e:
        raise ArchiveError(f"解压失败：{e}")

    return str(dest_p)


def find_dist_root(extract_dir: str) -> str:
    """在解压目录里定位真正的成品目录：含 game 文件夹的那一层。

    兼容三种常见结构：
    - 解压出来直接就是成品（含 game/）
    - 成品套在一个顶层文件夹里
    - 压缩包只打了成品内部文件（game/ 就在根）
    """
    root = Path(extract_dir)
    if (root / "game").is_dir():
        return str(root)
    # 往下最多找 3 层
    for depth_dir in [root] + [d for d in root.rglob("*") if d.is_dir()]:
        if (depth_dir / "game").is_dir():
            rel = depth_dir.relative_to(root)
            if len(rel.parts) <= 2:
                return str(depth_dir)
    raise ArchiveError(
        "解压后找不到成品目录（特征是里面有 game 文件夹）。"
        "请确认压缩包里是完整的 Ren'Py 发布成品。")


def create_zip(src_dir: str, dest_zip: str) -> str:
    """把目录打包成 zip（保持目录名作为顶层文件夹）。"""
    src = Path(src_dir)
    out = Path(dest_zip)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in src.rglob("*"):
            if p.is_file():
                zf.write(p, Path(src.name) / p.relative_to(src))
    return str(out)
