"""压缩包支持：zip / 7z / RAR / tar 系的识别、解压与重新打包。

- zip：Python 标准库直接搞定，支持密码
- 7z：纯 Python 库 py7zr，支持密码
- RAR：格式不开源，必须借助本机 7-Zip（7z.exe）；找不到时给出明确指引
- tar 系（.tar/.tar.bz2/.tbz2/.tar.gz/.tgz/.tar.xz）：官方 Linux 发布包
  默认就是 .tar.bz2（Issue #26），标准库 tarfile 直接处理；tar 不存在
  加密，password 对它无意义。解压一律按"不信任包内容"的口径校验，
  细节见 _extract_tar 的文档字符串
重新打包默认仍输出 zip（兼容性最好）；需要按后缀出 tar 系时用
create_archive，它会把 mode 一起写进成员，保证 0755 启动脚本可执行。
"""
from __future__ import annotations

import copy
import logging
import os
import shutil
import stat
import tarfile
import zipfile
from collections.abc import Collection
from pathlib import Path
from typing import Callable, Optional

from .procutil import run_quiet

logger = logging.getLogger(__name__)

# 支持的 tar 系后缀：官方 Linux 发布包的默认格式就是 .tar.bz2（Issue #26）。
# 长的放前面只是可读性考虑，_archive_suffix 做的是 endswith 匹配。
_TAR_SUFFIXES = (".tar.bz2", ".tar.gz", ".tar.xz", ".tbz2", ".tgz", ".tar")

ARCHIVE_EXTS = {".zip", ".7z", ".rar", *_TAR_SUFFIXES}


class ArchiveError(Exception):
    pass


def _archive_suffix(path: str) -> str:
    """取压缩包后缀（tar 系按最长后缀匹配）。

    为什么不直接用 Path(path).suffix：它只返回最后一段，
    Path("game.tar.bz2").suffix 是 ".bz2"——直接拿去比对会把官方
    Linux 发布包当成不认识的格式。这里先试 .tar.bz2 这类双后缀，
    再退回单后缀；统一转小写，Windows 上 .ZIP / .TAR.GZ 很常见。
    """
    name = Path(str(path)).name.lower()
    for suffix in _TAR_SUFFIXES:      # 双后缀在前，命中即返回
        if name.endswith(suffix):
            return suffix
    return Path(name).suffix


def archive_suffix(path: str) -> str:
    """公开入口：按最长后缀认压缩包格式（返回空串表示认不出）。

    pipeline / packager 也要按后缀认格式（回包沿用输入格式、统计打包
    产物），它们不该去摸私有名字，所以这里留一个一层转发的公开别名。
    """
    return _archive_suffix(path)


def is_archive(path: str) -> bool:
    return _archive_suffix(path) in ARCHIVE_EXTS


# 本工具能**写出**的格式。7z / RAR 只用于解包（RAR 没有可写实现，
# 7z 写入要外部工具），回包时不能沿用它们的后缀。
_WRITABLE_SUFFIXES = frozenset({".zip", ".tar", *_TAR_SUFFIXES})


def repack_suffix(input_suffix: str) -> str:
    """回包该用什么后缀：能原样写回的沿用，只读格式（.7z/.rar）回 zip。

    不这么做的话，7z/RAR 输入会在优化全部跑完后、最后打包一步才抛
    “不支持的打包格式”，用户白等一场。zip 兼容性最好，是安全默认。
    """
    return input_suffix if input_suffix in _WRITABLE_SUFFIXES else ".zip"


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

    tar 系走 _extract_tar：tar 格式本身不支持加密，password 参数对
    它无意义——忽略而不报错（顺手填了给 zip 用的密码不该被拒）。
    """
    src_p = Path(src)
    dest_p = Path(dest_dir)
    dest_p.mkdir(parents=True, exist_ok=True)
    ext = _archive_suffix(str(src_p))
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
                        # 归一化名 -> 先到条目原名（告警时引用）。
                        # 只有大小写不敏感的文件系统（Windows）才需要 casefold；
                        # POSIX 上 Logo.png 与 logo.png 是两个合法文件，
                        # 强行改名成 .dup1 等于让游戏缺资源。
                        def _norm(name: str) -> str:
                            normalized = name.replace("\\", "/")
                            return (normalized.casefold() if os.name == "nt"
                                    else normalized)
                        seen: dict[str, str] = {}
                        for info in infos:
                            _repair_zip_name(info)
                            norm = _norm(info.filename)
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
                                if _norm(cand_name) not in seen:
                                    break
                                n += 1
                            dup_info = copy.copy(info)
                            dup_info.filename = cand_name
                            seen[_norm(cand_name)] = cand_name
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
        elif ext in _TAR_SUFFIXES:
            # 官方 Linux 发布包（.tar.bz2）分支：安全策略全在 _extract_tar 里
            try:
                _extract_tar(src, dest_p)
            except tarfile.FilterError as exc:
                # 标准库 data 过滤器拦下的条目（穿越 / 绝对路径 / 特殊文件）：
                # 包内容确实不安全，直接报人话，别交出半个解压结果
                raise ArchiveError(
                    f"tar 压缩包里有不安全的条目（{exc}），"
                    "为避免写到目标目录之外，已中止解压。") from exc
            except tarfile.TarError as exc:
                raise ArchiveError(f"tar 压缩包损坏或格式异常：{exc}") from exc
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
    遍历口径已抽成 walk_tree / _walk_tree，与备份（backup.make_backup_zip）
    共用同一份实现——两处各写一份就是两份会各自漂移的安全判定。
    """
    src = Path(src_dir)
    out = Path(dest_zip)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        walk_tree(src, lambda p, rel: zf.write(str(p), rel.as_posix()),
                  lambda rel: zf.writestr(
                      zipfile.ZipInfo(rel.as_posix() + "/"), b""))
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


def walk_tree(root: str | Path,
              on_file: Callable[[Path, Path], None],
              on_empty_dir: Callable[[Path], None] | None = None,
              skip_names: Collection[str] = ()) -> None:
    """安全遍历一棵目录树，把普通文件逐个交给 on_file（唯一一份扫描口径）。

    口径（打包与备份共用，别在别处再写第二份）：
    - 用 os.scandir 自行递归，**不用 rglob**：rglob 会穿进符号链接与
      Windows junction，把范围外的文件当成树内普通文件；
    - 符号链接 / junction 无论指向文件还是目录一律跳过并告警，绝不跟随
      （成环会栈溢出，跟随则把工程外的数据拷进产物）；
    - skip_names 里的名字整棵跳过（目录跳过子树，同名文件也跳过）；
    - 空目录（或内容全被跳过、自身不是链接的目录）交给 on_empty_dir，
      否则解压后目录会凭空消失——根目录自身也算在内，与旧实现一致。

    传给回调的"相对路径"都带根目录名（root 的 basename 打头），因为交出去
    的 zip/tar 需要一个顶层文件夹，解压后才是 self-contained 的一份。
    参数：on_file(绝对路径, 相对 root 的路径)；on_empty_dir(相对 root 的路径)。
    """
    base = Path(root)
    _walk_tree(base, Path(base.name), on_file, on_empty_dir, set(skip_names))


def _walk_tree(dirpath: Path, rel: Path,
               on_file: Callable[[Path, Path], None],
               on_empty_dir: Callable[[Path], None] | None,
               skip_names: set[str]) -> None:
    """walk_tree 的递归体：rel 是 dirpath 在交付包里的相对路径（含根名）。"""
    entries: list[os.DirEntry] = []
    has_content = False
    with os.scandir(dirpath) as it:
        entries = sorted(it, key=lambda e: e.name)
    for entry in entries:
        if entry.name in skip_names:
            continue
        if _is_link_or_junction(entry):
            logger.warning("打包/备份跳过 '%s'：符号链接或 junction，"
                           "不跟随，避免成环或拷贝范围外内容。", entry.path)
            continue
        if entry.is_dir(follow_symlinks=False):
            _walk_tree(Path(entry.path), rel / entry.name,
                       on_file, on_empty_dir, skip_names)
            has_content = True
        elif entry.is_file(follow_symlinks=False):
            on_file(Path(entry.path), rel / entry.name)
            has_content = True
    if not has_content and on_empty_dir is not None:
        on_empty_dir(rel)


# ---------------------------------------------------------------------------
# tar 系：官方 Linux 发布包默认就是 .tar.bz2（Issue #26）
#
# 安全口径——tar 成员名由打包方随意书写，一律当不可信数据处理：
# - 名字是绝对路径、或 realpath 收敛后跑出 dest_dir 的，动手之前就跳过；
# - 符号链接 / 硬链接一律**不创建链接**，改为把链接目标的内容按普通文件
#   写出（理由写在 _tar_write_link_as_file 里）；
# - 设备文件 / FIFO / socket 跳过并告警，绝不按流打开（可能阻塞或写坏系统）；
# - 3.12+ 会让标准库 data 过滤器再过一层，它拦下的条目跳过并告警；真出错时
#   的 FilterError 由 extract_archive 转成人话，不交出半个解压结果。
# ---------------------------------------------------------------------------

def _member_output_path(dest_real: str, name: str) -> Optional[str]:
    """把 tar 成员名收敛成 dest_dir 内的落地路径；不安全返回 None。

    两道判据缺一不可：
    1. 名字本身不是绝对路径（含 Windows 的 C:/ 与 UNC）、不带 ".."，
       也不含 NUL——这些是路径穿越的典型写法；
    2. 松手用 os.path.realpath 收敛后（解掉 "a/../b"、"./"、"//" 这些
       花样），结果必须仍在 dest_dir 之内。这里按路径分量比而不是
       str.startswith：否则 "dest_evil" 这种同前缀的兄弟目录会被放过去。
       realpath 同时解掉了父目录里已存在的符号链接，防止"包外链接 +
       包内路径"的组合拳。
    """
    if not name or "\x00" in name:
        return None
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or os.path.isabs(normalized):
        return None
    if ".." in normalized.split("/"):
        return None
    target = os.path.realpath(os.path.join(dest_real, normalized))
    if target != dest_real and not target.startswith(dest_real + os.sep):
        return None
    return target


def _tar_set_mode(path: str, mode: Optional[int], is_dir: bool) -> None:
    """按 tar 成员的 mode 还原权限位（核心诉求：renpy.sh 解压后仍是 0755）。

    tar 是 Unix 格式，官方 Linux 包的启动脚本 renpy.sh 必须可执行，
    否则解压出来根本跑不起来；而 zip 存不住 Unix 权限位，这也是
    Linux 发布包偏爱 tar 的原因。

    只搬权限那 12 位（S_IMODE），不碰文件类型位；目录的 mode 不还原
    （临时目录得让当前进程继续往里写，保持默认更省事）。

    失败一律静默：Windows 的 os.chmod 只认只读位（S_IWRITE），对 0755
    可能直接抛错或只生效一半——这不是错误，不该让整次解压失败。
    """
    if is_dir or not mode:          # mode 为 None 或极端包里的全 0，跳过
        return
    try:
        # 只还原普通读写执行位，绝不还原 setuid/setgid/sticky：
        # 不可信包不能往用户目录里写 setuid 文件；3.12 的 data_filter
        # 也只保留 0o755，两个版本口径必须一致。
        os.chmod(path, stat.S_IMODE(mode) & 0o755)
    except OSError:
        pass


def _tar_link_source(dest_real: str, member: tarfile.TarInfo,
                     member_path: str) -> Optional[str]:
    """算出链接成员"指向的内容"在 dest_dir 内的真实路径；不安全返回 None。

    解析规则按 tar 规范：
    - 符号链接（SYMTYPE）的目标相对**链接所在目录**；
    - 硬链接（LNKTYPE）的目标相对**包根**。

    解析后同样用 realpath 收敛再确认仍在 dest_dir 之内——安全红线是
    "绝不跟随链接写到 dest_dir 之外"，读也一样：只从包内读。
    """
    link = (member.linkname or "").replace("\\", "/")
    if not link or link.startswith("/") or os.path.isabs(link):
        return None
    base = os.path.dirname(member_path) if member.issym() else dest_real
    source = os.path.realpath(os.path.join(base, link))
    if source != dest_real and not source.startswith(dest_real + os.sep):
        return None
    return source


def _tar_write_link_as_file(tf: tarfile.TarFile, member: tarfile.TarInfo,
                            member_path: str, dest_real: str) -> bool:
    """把链接成员按普通文件写出（复制目标内容），返回是否写出。

    策略选择：**不创建链接，改为复制目标内容**，而不是一跳了之。
    理由：
    - 安全红线是"绝不跟随链接写到 dest_dir 之外"。复制时我们只从包内
      已校验过的路径读、写到成员自己（已校验）的名字上，全程不调用
      os.symlink / os.link，从根上没有"跟随外链写出去"这条路；
    - 官方 Linux 发布包里带链接是常态（发行流程里给解释器/库做软链），
      直接跳过会解出缺文件、跑不起来的半成品；复制能保住"能跑"，
      代价只是多占一份空间；
    - 硬链接在 Windows 上还要额外权限，复制是唯一跨平台行为一致的选择。

    调用时机：由 _extract_tar 在**全部普通成员落盘之后**统一调用（见那里的
    "两趟"说明）。目标仍不在包里时才告警跳过——宁可缺这一项，也不去猜内容。
    """
    source = _tar_link_source(dest_real, member, member_path)
    if source is None:
        logger.warning("tar 包里的链接 '%s' -> '%s' 指向目标目录之外"
                       "（或目标为空），已跳过：绝不跟随包外路径读写。",
                       member.name, member.linkname)
        return False
    try:
        if member.issym():
            if not os.path.isfile(source):
                raise OSError("目标不是已解压出来的普通文件")
            with open(source, "rb") as fsrc, open(member_path, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst)
        else:                       # 硬链接：内容就在包里的另一个成员
            src_member = tf.getmember(member.linkname)
            if not src_member.isreg():
                raise OSError("硬链接目标不是普通文件")
            fsrc = tf.extractfile(src_member)
            if fsrc is None:
                raise OSError("硬链接目标读不出内容")
            with fsrc, open(member_path, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst)
    except (OSError, KeyError, tarfile.TarError) as exc:
        logger.warning("tar 包里的链接 '%s' -> '%s' 没能还原内容（%s），"
                       "已跳过；需要时请从原始压缩包重新解压该文件。",
                       member.name, member.linkname, exc)
        return False
    _tar_set_mode(member_path, member.mode, False)
    return True


def _extract_tar(src: str, dest_dir: Path) -> None:
    """解压 tar / tar.bz2 / tar.gz / tar.xz 到 dest_dir（策略见本节头部注释）。

    为什么逐成员自己走，而不是一句 tarfile.extractall(filter="data")：
    data 过滤器是 3.12 才有的（3.11 上回退时照样得手写同样的校验），
    而且它对链接只要求"目标不许指向包外"，仍会真的把链接创建出来；
    我们要的是更保守的口径——内容落地成普通文件、绝不创建链接。
    在 3.12+ 上仍然先让标准库 data 过滤器过一遍（双层校验：它顺带剥掉
    group/other 写位、清掉属主信息），被它拦下的条目跳过并告警；
    3.11 没有这个参数（强传只会 TypeError），就只跑我们自己的校验。

    **两趟**：普通成员先落盘，链接成员留到第二趟再还原。tar 允许链接排在
    目标前面（官方 Ren'Py Linux 包里 `game/xxx.so` → `lib/xxx.so.1` 这类
    软链就是常态，且按名字排序常常排在目标之前）。若边遍历边还原，目标尚未
    解出会被判"不是已解压出来的普通文件"而丢弃，解出来的成品缺文件、跑不
    起来——静默残缺比直接报错更难查。两趟之后所有普通文件都在盘上，
    还原只取决于目标是否真的在包里。
    """
    dest_real = os.path.realpath(str(dest_dir))
    # 第一趟：普通文件/目录落盘；链接成员记下来，等第二趟再还原
    # （tar 允许链接排在目标之前，边遍历边还原会丢掉这类条目）。
    pending_links: list[tuple[tarfile.TarInfo, str]] = []
    with tarfile.open(src, "r:*") as tf:     # "r:*" 让标准库自己嗅探压缩方式
        supports_filter = hasattr(tarfile, "data_filter")
        for member in tf:
            raw_name = member.name
            if supports_filter:
                try:
                    filtered = tarfile.data_filter(member, dest_real)
                except tarfile.FilterError as exc:
                    logger.warning(
                        "tar 包里的条目 '%s' 没通过标准库 data 过滤器（%s），"
                        "已跳过。", raw_name, exc)
                    continue
                if filtered is None:         # 过滤器返回 None 表示整条跳过
                    continue
                member = filtered
            # 路径校验放在任何写盘动作之前：先判后动
            member_path = _member_output_path(dest_real, raw_name)
            if member_path is None:
                logger.warning(
                    "tar 包里的条目 '%s' 是绝对路径或会写到目标目录之外，"
                    "已跳过（不创建任何文件）。", raw_name)
                continue
            try:
                if member.isdir():
                    os.makedirs(member_path, exist_ok=True)
                elif member.isreg():
                    parent = os.path.dirname(member_path)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    fsrc = tf.extractfile(member)
                    if fsrc is None:         # 空成员：照样落一个空文件
                        Path(member_path).write_bytes(b"")
                    else:
                        with fsrc, open(member_path, "wb") as fdst:
                            shutil.copyfileobj(fsrc, fdst)
                    _tar_set_mode(member_path, member.mode, False)
                elif member.issym() or member.islnk():
                    # 只登记，第二趟再还原（此时目标一定已经落盘）
                    pending_links.append((member, member_path))
                else:
                    # 设备文件 / FIFO 等特殊条目：绝不按流打开（可能阻塞或
                    # 写坏系统）。3.12+ 的 data 过滤器已经拦过一次，3.11 兜底
                    logger.warning("tar 包里的条目 '%s' 是设备文件 / FIFO 等"
                                   "特殊条目，已跳过。", raw_name)
            except (OSError, tarfile.TarError) as exc:
                logger.warning("tar 包里的条目 '%s' 解压失败（%s），已跳过。",
                               raw_name, exc)

        # 第二趟：还原链接成员。必须在同一个 tarfile 上下文里做——硬链接
        # 还要回读包内容（tf.extractfile），出了 with 就拿不到句柄了。
        for member, member_path in pending_links:
            parent = os.path.dirname(member_path)
            if parent:
                try:
                    os.makedirs(parent, exist_ok=True)
                except OSError as exc:
                    logger.warning("tar 包里的链接 '%s' 建父目录失败（%s），"
                                   "已跳过。", member.name, exc)
                    continue
            try:
                _tar_write_link_as_file(tf, member, member_path, dest_real)
            except (OSError, tarfile.TarError) as exc:
                logger.warning("tar 包里的链接 '%s' 还原失败（%s），已跳过。",
                               member.name, exc)


# tar 后缀 -> tarfile 写入模式。放在这里而不是藏进函数里，方便一眼对账
_TAR_WRITE_MODES = {
    ".tar": "w",
    ".tar.bz2": "w:bz2",
    ".tbz2": "w:bz2",
    ".tar.gz": "w:gz",
    ".tgz": "w:gz",
    ".tar.xz": "w:xz",
}


def create_archive(src_dir: str, dest_archive: str) -> str:
    """把目录打包成 dest_archive：.zip 走 create_zip，tar 系走 tarfile。

    Issue #26：官方 Linux 发布包就是 .tar.bz2，回包再一律给 zip 会跟用户
    原来的发布流程对不上（Linux 玩家下下来发现不是 tar.bz2）。这里按后缀
    分派，认不出的后缀直接报错——不猜。

    tar 系会把源文件权限写进 tar 成员（tarinfo.mode），保证 0755 的启动
    脚本往返一次还是 0755（zip 格式存不住 Unix 权限位，解出来没有执行位）。
    链接 / junction 沿用 create_zip 的口径：跳过并告警，不跟随打包。
    """
    suffix = _archive_suffix(str(dest_archive))
    if suffix == ".zip":
        return create_zip(src_dir, dest_archive)    # zip 行为保持不变
    if suffix in _TAR_SUFFIXES:
        return _create_tar(src_dir, dest_archive, suffix)
    raise ArchiveError(f"不支持的打包格式：{dest_archive}")


def _create_tar(src_dir: str, dest_archive: str, suffix: str) -> str:
    """tar 系打包入口：按后缀选压缩方式，递归加目录。"""
    src = Path(src_dir)
    out = Path(dest_archive)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(str(out), _TAR_WRITE_MODES[suffix]) as tf:
        _tar_add_dir(tf, src, Path(src.name))
    return str(out)


def _tar_member_mode(st: os.stat_result) -> int:
    """把源文件权限搬进 tarinfo.mode。

    Linux/macOS 上老实用 stat 的权限位（0755 的脚本原样搬走）；
    Windows 上 stat 那一套是假的（恒 0666/0777，没有执行位可言），
    照搬会让所有文件都"看起来可执行"，所以按 Unix 惯例给 0644 兜底——
    需要可执行的是脚本，而脚本本来就得在 Unix 上打出来。
    """
    if os.name == "nt":
        return 0o644
    return stat.S_IMODE(st.st_mode) or 0o644


def _tar_add_dir(tf: tarfile.TarFile, dirpath: Path, rel: Path) -> None:
    """递归把一个目录加入 tar：跳过符号链接/junction，空目录保留条目。

    遍历走 walk_tree（与 create_zip 同一份 os.scandir 递归实现，不跟随
    链接），区别只在于 tar 成员带上 mode/mtime，权限才能往返。
    """
    def add_file(path: Path, member_rel: Path) -> None:
        st = path.stat(follow_symlinks=False)
        info = tarfile.TarInfo(member_rel.as_posix())
        info.size = st.st_size
        info.mode = _tar_member_mode(st)
        info.mtime = int(st.st_mtime)
        with open(path, "rb") as fh:
            tf.addfile(info, fh)

    def add_empty_dir(member_rel: Path) -> None:
        # 空目录（或内容全被跳过）：写一个目录成员，解压后仍然存在
        info = tarfile.TarInfo(member_rel.as_posix() + "/")
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        tf.addfile(info)

    walk_tree(dirpath, add_file, add_empty_dir)
