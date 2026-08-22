"""压缩包支持：zip / 7z / RAR 的识别、解压与重新打包。

- zip：Python 标准库直接搞定，支持密码
- 7z：纯 Python 库 py7zr，支持密码
- RAR：格式不开源，必须借助本机 7-Zip（7z.exe）；找不到时给出明确指引
重新打包统一输出 zip（兼容性最好）。
"""
from __future__ import annotations

import copy
import logging
import os
import shutil
import stat
import zipfile
from pathlib import Path
from typing import Optional

from .procutil import run_quiet

logger = logging.getLogger(__name__)

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


def _repair_zip_name(info: zipfile.ZipInfo) -> None:
    """还原未置 UTF-8 标志条目的中文文件名（审核修复 严重-2）。

    国产压缩工具（资源管理器/WinRAR/好压/360/Bandizip 默认项）
    打包中文文件名用 GBK 且不置 UTF-8 标志，Python zipfile 按
    cp437 解码，中文全部变乱码——落盘后资源加载失败，回包时
    乱码还会永久化。修法：未置标志的条目先用 cp437 还原原始
    字节，再按 utf-8 → gb18030 回解，成功则覆写文件名；均失败
    保持原名（真西文名不受影响）。
    """
    if info.flag_bits & 0x800:      # 已置 UTF-8 标志，名字无需修复
        return
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return
    for enc in ("utf-8", "gb18030"):
        try:
            name = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if not name or "\x00" in name:
            return
        info.filename = name
        return


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
            # 审核修复（中-18）：中文 Windows 工具可能把中文密码按 GBK
            # 编码，强制 utf-8 必然解密失败；失败后依次重试 gbk/cp437
            pwds = [pwd] if pwd else [None]
            if pwd:
                for enc in ("gbk", "cp437"):
                    try:
                        cand = password.encode(enc)
                    except UnicodeEncodeError:
                        continue
                    if cand not in pwds:
                        pwds.append(cand)
            ok = False
            for cand in pwds:
                try:
                    with zipfile.ZipFile(src) as zf:
                        infos = zf.infolist()
                        # 审核修复（中-18）：WinZip AES（compress_type=99）
                        # zipfile 不支持，明说而非误报"压缩包损坏"
                        if any(i.compress_type == 99 for i in infos):
                            raise ArchiveError(
                                "这个压缩包用了 WinZip AES 加密，本工具暂不支持。"
                                "请改用其他工具重新压缩（选标准 ZipCrypto 加密），"
                                "或自行解压后选择文件夹处理。")
                        # 审核修复：撞名防护——多个条目归一化名收敛时（Windows
                        # 大小写不敏感撞名、完全重复条目、GBK/UTF-8 回解后
                        # 收敛为同名），先到条目照原样解压，后到条目改名带
                        # .dup{N} 后缀保留内容并告警，避免静默覆盖丢数据。
                        # 归一化名 -> 先到条目原名（告警时引用）
                        seen: dict[str, str] = {}
                        for info in infos:
                            _repair_zip_name(info)
                            norm = info.filename.replace("\\", "/").casefold()
                            first = seen.get(norm)
                            if first is None:
                                seen[norm] = info.filename
                                zf.extract(info, dest_p, pwd=cand)
                                continue
                            if info.is_dir():
                                logger.warning(
                                    "压缩包撞名：目录条目 '%s' 与已解压条目 '%s' "
                                    "冲突，跳过。", info.filename, first)
                                continue
                            n = 1
                            while True:
                                cand_name = f"{info.filename}.dup{n}"
                                if cand_name.replace("\\", "/").casefold() not in seen:
                                    break
                                n += 1
                            dup_info = copy.copy(info)
                            dup_info.filename = cand_name
                            seen[cand_name.replace("\\", "/").casefold()] = cand_name
                            logger.warning(
                                "压缩包撞名：条目 '%s' 与已解压条目 '%s' 冲突，"
                                "后到内容保留为 '%s'。", info.filename, first, cand_name)
                            zf.extract(dup_info, dest_p, pwd=cand)
                    ok = True
                    break
                except RuntimeError as e:
                    if "password" in str(e).lower():
                        continue     # 密码不对：试下一种密码编码
                    raise ArchiveError(f"压缩包损坏或格式异常：{e}")
            if not ok:
                raise ArchiveError("密码不对，解压失败。请检查密码后重试。")
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
                # 审核修复（中-17）：stdin 已隔离，加密 RAR 无密码时
                # 会快速失败而非挂死；把"可能缺密码"说清楚
                if not password:
                    raise ArchiveError(
                        f"RAR 解压失败。如果这个压缩包有密码，请在高级选项里"
                        f"填写后重试。（{err}）")
                raise ArchiveError(f"RAR 解压失败（密码错误或文件损坏）：{err}")
        else:
            raise ArchiveError(f"不支持的压缩包类型：{ext}")
    except ArchiveError:
        raise
    except Exception as e:
        raise ArchiveError(f"解压失败：{e}")

    return str(dest_p)


def find_dist_roots(extract_dir: str) -> list[str]:
    """在解压目录里定位全部成品根（含 game 文件夹的那一层）。

    审核修复（高-4）：多平台发布包（PC+Mac 三合一等）里有多个
    game 目录，旧版只返回一个、其余平台被静默丢弃；现返回全部
    候选，由调用方决定逐个处理还是要求拆包。

    兼容三种常见结构：
    - 解压出来直接就是成品（含 game/）
    - 成品套在顶层文件夹里
    - Mac 版 .app 包：game 藏在 Contents/Resources/autorun/ 多层深处
    排序规则："长得像游戏目录"（含脚本/gui/图片）优先，再按深度。
    """
    root = Path(extract_dir)
    if (root / "game").is_dir():
        return [str(root)]

    candidates = [d for d in root.rglob("game") if d.is_dir()]

    def looks_real(d: Path) -> bool:
        if any((d / x).exists() for x in ("scripts", "gui", "images", "audio")):
            return True
        return any(d.glob("*.rpyc")) or any(d.glob("*.rpy"))

    real = [d for d in candidates if looks_real(d)]
    pool = real or candidates
    if not pool:
        raise ArchiveError(
            "解压后找不到成品目录（特征是里面有 game 文件夹）。"
            "请确认压缩包里是完整的 Ren'Py 发布成品。")
    pool.sort(key=lambda d: len(d.parts))
    # 返回"包含 game 的那一层"（成品根），不是 game 本身；去重保序
    roots: list[str] = []
    for g in pool:
        r = str(g.parent)
        if r not in roots:
            roots.append(r)
    return roots


def find_dist_root(extract_dir: str) -> str:
    """定位"最可能是主成品"的根（单成品场景兼容入口）。"""
    return find_dist_roots(extract_dir)[0]


def create_zip(src_dir: str, dest_zip: str) -> str:
    """把目录打包成 zip（保持目录名作为顶层文件夹）。

    压缩等级 9：交付包能榨一丝是一丝；包内大头（图片/音频/封包）
    本身已是压缩格式，收益很小但零风险，多核机器上耗时也可接受。

    审核修复：改用 os.scandir 自行递归（rglob 会穿入 Windows junction，
    可能成环或拉进范围外内容），跳过符号链接与 junction 并告警；
    空目录写入目录条目（arcname 以 / 结尾）避免解压后丢失。
    """
    src = Path(src_dir)
    out = Path(dest_zip)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        _zip_add_dir(zf, src, Path(src.name))
    return str(out)


def _is_link_or_junction(entry: os.DirEntry) -> bool:
    """判断目录项是否为符号链接 / junction（一律不跟随）。

    审核修复：旧版用 os.path.islink，在 Windows junction 上返回 False，
    且 entry.is_dir(follow_symlinks=False) 返回 True，防护形同虚设，
    junction 被递归穿入（成环时直接栈溢出）。现改用 os.lstat：
    S_ISLNK 覆盖普通符号链接；Windows 专属再查
    FILE_ATTRIBUTE_REPARSE_POINT 覆盖 junction（需 hasattr 平台判断）。
    """
    st = os.lstat(entry.path)
    if stat.S_ISLNK(st.st_mode):
        return True
    if hasattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT"):
        attrs = getattr(st, "st_file_attributes", 0) or 0
        if attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            return True
    return False


def _zip_add_dir(zf: zipfile.ZipFile, dirpath: Path, rel: Path) -> None:
    """递归把一个目录加入 zip：跳过符号链接/junction，空目录保留条目。"""
    has_content = False
    with os.scandir(dirpath) as it:
        for entry in sorted(it, key=lambda e: e.name):
            if _is_link_or_junction(entry):
                logger.warning("打包跳过 '%s'：符号链接或 junction，"
                               "不跟随，避免成环或拷贝范围外内容。", entry.path)
                continue
            if entry.is_dir(follow_symlinks=False):
                _zip_add_dir(zf, Path(entry.path), rel / entry.name)
                has_content = True
            elif entry.is_file(follow_symlinks=False):
                zf.write(entry.path, rel / entry.name)
                has_content = True
    if not has_content:
        zf.writestr(zipfile.ZipInfo(rel.as_posix() + "/"), b"")
