"""APK 瘦身（BACKLOG F1，实验性）。

Ren'Py 安卓版结构：assets/x-game/** 是游戏资源，assets/x-renpy/** 是引擎。
策略（同名同格式，绝不改名）：
- 只压 assets/x-game/ 下的图片/音频/字体；引擎目录一律不碰
- 字体字符集来自 APK 内的编译脚本（x-scripts/x-tl 的 rpyc）+ 保底字符集
- 重打包时未改动的条目保留原内容与原压缩方式标记（审核修复
  中-16：DEFLATE 条目经 zipfile 重压字节流可能变，"逐字节保留"
  仅对 STORED 条目成立），只替换改动项
- 改动后原签名必然失效：删除旧签名；提供了钥匙和密码就用
  Android SDK 的 apksigner 重签，否则产出未签名包并明确警告
"""
from __future__ import annotations

import logging
import os
import random
import shutil
import string
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from . import charset as charset_mod
from . import remap as remap_mod
from .audio_optimizer import convert_audio, reencode_audio
from .config import PRESETS, DEFAULT_PRESET, CharsetOptions
from .font_optimizer import subset_font
from .image_optimizer import optimize_image
from .models import AssetKind, Progress, kind_of
from .procutil import run_quiet
from .utils import find_suffix_clashes, safe_join

logger = logging.getLogger(__name__)

# 引擎与杂项目录（APK 内相对路径前缀），一律不碰
UNTOUCHABLE_PREFIXES = ("assets/x-renpy/", "assets/dexopt/", "lib/", "res/",
                        "kotlin/", "META-INF/")
SIGNATURE_SUFFIXES = (".SF", ".RSA", ".DSA", ".EC")
X_GAME_PREFIX = "assets/x-game/"


class ApkError(Exception):
    pass


def apk_entry_to_game_rel(entry: str) -> Optional[str]:
    """APK 条目名 → 游戏内相对路径（去掉 x- 前缀体系）。

    assets/x-game/x-images/x-foo.png -> images/foo.png
    非 x-game 下的条目返回 None。
    """
    if not entry.startswith(X_GAME_PREFIX):
        return None
    parts = entry[len(X_GAME_PREFIX):].split("/")
    parts = [seg[2:] if seg.startswith("x-") else seg for seg in parts]
    return "/".join(parts)


def game_rel_to_apk_entry(rel: str) -> str:
    """游戏内相对路径 → APK 条目名（每个段加 x- 前缀）。

    images/foo.webp -> assets/x-game/x-images/x-foo.webp
    """
    parts = rel.split("/")
    return X_GAME_PREFIX + "/".join("x-" + p for p in parts)


def compile_remap_rpyc(script_text: str, sdk: str) -> Optional[bytes]:
    """用 SDK 把重映射脚本编译成 rpyc 字节（APK 里只认编译产物）。"""
    renpy_exe = Path(sdk) / "renpy.exe"
    if not renpy_exe.exists():
        return None
    proj = Path(tempfile.mkdtemp(prefix="renpyslim_rpyc_"))
    try:
        game = proj / "game"
        game.mkdir()
        (game / remap_mod.REMAP_SCRIPT_NAME).write_text(script_text,
                                                        encoding="utf-8")
        run_quiet([str(renpy_exe), str(proj), "compile"],
                  capture_output=True, timeout=300, cwd=str(sdk))
        rpyc = game / (remap_mod.REMAP_SCRIPT_NAME + "c")
        return rpyc.read_bytes() if rpyc.exists() else None
    except Exception:
        return None
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def _bt_version(d: Path) -> Optional[tuple]:
    """build-tools 目录名 → 数字版本元组（"35.0.0" → (35,0,0)），
    解析失败返回 None（排序时垫底）。不能用字符串排序：
    "9.0.0" 字典序大于 "35.0.0" 会选错版本。"""
    try:
        return tuple(int(x) for x in d.name.split("."))
    except ValueError:
        return None


def find_build_tools(sdk: str) -> tuple[Optional[str], Optional[str]]:
    """在 SDK 的 rapt/Sdk/build-tools 里找 zipalign 和 apksigner。"""
    bt_root = Path(sdk) / "rapt" / "Sdk" / "build-tools"
    if not bt_root.is_dir():
        return None, None

    def _key(d: Path):
        v = _bt_version(d)
        # 可解析的 (1, 版本元组) 优先；解析失败的 (0, ()) 排最后
        return (1, v) if v is not None else (0, ())

    versions = sorted((d for d in bt_root.iterdir() if d.is_dir()),
                      key=_key, reverse=True)
    for d in versions:
        za = d / "zipalign.exe"
        signer = d / "apksigner.bat"
        if za.exists() and signer.exists():
            return str(za), str(signer)
    return None, None


def _has_non_ascii(s: str) -> bool:
    return any(ord(c) > 127 for c in s)


def _short_path_fallback(p: Path, what: str,
                         warnings: Optional[list[str]] = None) -> Path:
    """非 ASCII 路径兜底：Windows 上试取 8.3 短路径。

    背景：apksigner(Java) 对含乱码/生僻字符的路径会报 Bad pathname。
    短路径仍含非 ASCII 或调用失败时保留原路径，只打警告不阻断。
    """
    if not _has_non_ascii(str(p)):
        return p
    if os.name != "nt":
        logger.warning("%s路径含非 ASCII 字符（非 Windows 无短路径兜底）：%s",
                       what, p)
        return p
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(p), buf, 32768)
        if 0 < n < 32768 and not _has_non_ascii(buf.value):
            logger.info("%s路径含非 ASCII 字符，已改用 8.3 短路径：%s -> %s",
                        what, p, buf.value)
            return Path(buf.value)
        msg = f"{what}路径含非 ASCII 字符且无法取得纯英文短路径，按原路径继续。"
    except Exception as e:
        msg = f"{what}路径含非 ASCII 字符，短路径兜底失败（{e}），按原路径继续。"
    logger.warning("%s", msg)
    if warnings is not None:
        warnings.append(msg)
    return p


def _find_java() -> Optional[str]:
    """找 java：PATH -> JAVA_HOME。"""
    found = shutil.which("java")
    if found:
        return found
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        name = "java.exe" if os.name == "nt" else "java"
        cand = Path(java_home) / "bin" / name
        if cand.exists():
            return str(cand)
    return None


# 回退 .bat 时密码里出现这些字符会被 cmd.exe 解释（拆参/注入）
_CMD_DANGEROUS = set('"%&|<>^')


def _apksigner_cmd(signer: str) -> list:
    """拼 apksigner 调用命令。

    审核修复（中-14）：优先 java -jar 直调——apksigner.bat 由 cmd.exe
    解释命令行，密码/路径含 \" % & 等字符会拆断参数甚至命令注入；
    java -jar 无 shell 层，参数按列表直传。
    """
    jar = Path(signer).parent / "lib" / "apksigner.jar"
    java = _find_java()
    if jar.exists() and java:
        return [java, "-jar", str(jar)]
    return [signer]


def find_keytool() -> Optional[str]:
    """找 keytool（JDK 自带）：PATH -> JAVA_HOME -> 常见安装目录。"""
    kt = shutil.which("keytool")
    if kt:
        return kt
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        name = "keytool.exe" if os.name == "nt" else "keytool"
        cand = Path(java_home) / "bin" / name
        if cand.exists():
            return str(cand)
    for base in (Path(r"C:\Program Files\Eclipse Adoptium"),
                 Path(r"C:\Program Files\Java"),
                 Path(r"C:\Program Files (x86)\Java")):
        if base.is_dir():
            for exe in base.rglob("keytool.exe"):
                return str(exe)
    return None


def generate_keystore(dest_dir: str, password: Optional[str] = None,
                      alias: str = "renpyslim") -> dict:
    """现场生成一把新 keystore（模式③）。

    密码不传则自动生成随机密码；密码写进钥匙旁边的备忘文件，
    请用户妥善保管——丢了就无法再给这个应用签更新包。
    """
    kt = find_keytool()
    if not kt:
        raise ApkError("未找到 keytool（生成钥匙需要 JDK）。请先安装 JDK。")
    password = password or "".join(
        random.choices(string.ascii_letters + string.digits, k=14))
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    ks = dest / "renpyslim.keystore"
    if ks.exists():
        ks = dest / f"renpyslim-{int(__import__('time').time())}.keystore"
    cmd = [kt, "-genkeypair", "-keystore", str(ks), "-alias", alias,
           "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
           "-storepass", password, "-keypass", password,
           "-dname", "CN=RenPySlim, O=RenPySlim"]
    proc = run_quiet(cmd, capture_output=True, timeout=120)
    if proc.returncode != 0 or not ks.exists():
        err = proc.stderr.decode("utf-8", "replace")[-300:]
        raise ApkError(f"生成钥匙失败：{err}")
    memo = ks.with_name(ks.stem + "-钥匙备忘.txt")
    memo.write_text(
        "RenPySlim 自动生成的签名钥匙（请妥善保管！）\n\n"
        f"钥匙文件：{ks}\n"
        f"别　　名：{alias}\n"
        f"密　　码：{password}\n\n"
        "重要：\n"
        "1. 这个密码是打开钥匙的唯一凭据，丢了就无法再给这个应用签更新包。\n"
        "2. 用这把钥匙签的包，玩家需先卸载旧版再安装（签名不同不能覆盖更新）。\n"
        "3. 建议把钥匙文件和这份备忘一起备份到安全的地方。\n",
        encoding="utf-8")
    return {"keystore": str(ks), "password": password, "alias": alias,
            "memo": str(memo)}


def _extract_charset_from_apk(extract_root: Path,
                              opts: CharsetOptions) -> set[str]:
    """从 APK 里的编译脚本提取字符集。

    编译产物（rpyc/rpymc）走 zlib 解压宽容解码；纯文本（rpy/txt）
    走稳健文本读取——以前全部按 rpyc 解，文本文件 zlib 失败
    返回空串，白白丢字符。（审核修复）
    """
    chars: set[str] = set()
    for p in extract_root.rglob("*"):
        if not p.is_file():
            continue
        suffix = p.suffix.lower()
        if suffix in (".rpyc", ".rpymc"):
            text = charset_mod.read_rpyc_text(p)
        elif suffix in (".rpy", ".txt"):
            text = charset_mod.read_text_robust(p)
        else:
            continue
        chars.update(c for c in text if c.isprintable())
    chars.update(opts.base_text())
    chars.discard("\x00")
    return chars


def slim_apk(apk_path: str, preset_name: str,
              charset_opts: Optional[CharsetOptions] = None,
              sdk: Optional[str] = None,
              keystore: Optional[str] = None,
              ks_pass: Optional[str] = None,
              key_alias: Optional[str] = None,
              key_pass: Optional[str] = None,
              generate_key: bool = False,
              new_key_password: Optional[str] = None,
              remap_convert: bool = False,
              progress: Optional[Progress] = None) -> dict:
    """瘦身一个 APK。签名三种姿势：
    ① 传 keystore+ks_pass → 用原钥匙（可覆盖安装旧版）
    ② 传自有 keystore+密码 → 用自己的身份签
    ③ generate_key=True → 现场生成新钥匙+密码备忘（新身份，需卸载重装）
    都不传 → 未签名产出 + 警告。
    remap_convert=True（实验性）：图片转 WebP、音频转 OGG，并注入编译好的
    重映射脚本把请求透明指到新文件（B9 的 APK 版，不改任何引用）。
    返回 {output, saved_bytes, changes, signed, keystore, warnings}。
    """
    p = progress or Progress()
    preset = PRESETS.get(preset_name, PRESETS[DEFAULT_PRESET])
    cs_opts = charset_opts or CharsetOptions()

    apk = Path(apk_path)
    if not apk.exists():
        raise ApkError(f"APK 不存在：{apk_path}")

    warnings: list[str] = []
    # 非 ASCII 兜底：工作目录落在含乱码/生僻字符的用户名路径下时，
    # 后续 zipalign/apksigner 可能报 Bad pathname，改用 8.3 短路径（同目录）
    work = _short_path_fallback(
        Path(tempfile.mkdtemp(prefix="renpyslim_apk_")), "临时工作目录",
        warnings)
    changes = 0
    saved = 0
    zf = None
    try:
        zf = zipfile.ZipFile(str(apk))
        names = zf.namelist()

        # 1. 提取可优化目标
        targets = []
        script_entries = []   # x-game 内脚本/文本：只供字符集提取，不参与优化
        for name in names:
            if name.endswith("/"):
                continue
            if any(name.startswith(pre) for pre in UNTOUCHABLE_PREFIXES):
                continue
            if not name.startswith("assets/"):
                continue
            if kind_of(Path(name).suffix.lower()) == AssetKind.OTHER:
                # 审核修复：rpyc/rpy/txt 属 OTHER 以前从不解出，字符集扫空，
                # 中文字体被剃成保底集（文字变方框）
                if name.startswith(X_GAME_PREFIX) and \
                        Path(name).suffix.lower() in (".rpyc", ".rpymc",
                                                      ".rpy", ".txt"):
                    script_entries.append(name)
                continue
            targets.append(name)
        p.emit("apk", f"APK 共 {len(names)} 个条目，可优化资源 {len(targets)} 个")

        skipped: set[str] = set()        # 审核修复（中-11）：被拒/解出失败的条目
        extracted_names: set[str] = set()  # 已解出到 work 的条目（中-16 复用）
        for name in targets + script_entries:
            # 审核修复：条目名来自不可信输入，净化后再落盘（防 zip-slip）
            out = safe_join(work, name)
            if out is None:
                warnings.append(f"条目名异常（疑似路径穿越），已跳过：{name}")
                skipped.add(name)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(name) as src_f:
                    out.write_bytes(src_f.read())
            except (OSError, KeyError, RuntimeError,
                    zipfile.BadZipFile) as e:
                # 审核修复（中-11）：解出失败也得移出 targets，否则
                # 优化循环 stat 直接 FileNotFoundError 崩掉整个瘦身
                warnings.append(f"条目解出失败，已跳过：{name}（{e}）")
                skipped.add(name)
                continue
            extracted_names.add(name)
        if skipped:
            targets = [n for n in targets if n not in skipped]

        # 2. 从脚本提取字符集（供字体瘦身）
        chars = _extract_charset_from_apk(work, cs_opts)
        p.emit("apk", f"从 APK 内脚本提取到 {len(chars)} 个字符")

        # 3. 逐个同名优化（开了 remap_convert 则图/音先试换格式）
        remap_usable = remap_convert and bool(sdk) \
            and (Path(sdk) / "renpy.exe").exists()
        if remap_convert and not remap_usable:
            warnings.append("未找到 Ren'Py SDK，无法编译重映射脚本，"
                            "图片/音频降级为同名压缩。")
        changed: set[str] = set()
        pending_remap = []   # (旧条目, 新条目名, 本地新文件, 节省字节)
        # 审核修复：预检同名撞车——foo.png 与 foo.jpg 都会变 foo.webp，
        # 撞车项不转换（降级同名压缩），避免互覆丢文件
        # 审核修复（中-33）：参照集合并入全部资源名，转换目标与
        # 现存资源同名（如 foo.png 撞上已有的 foo.webp）也拦截
        all_game_rels = [r for r in (apk_entry_to_game_rel(n) for n in targets)
                         if r]
        clash_webp = find_suffix_clashes(
            [r for r in all_game_rels
             if Path(r).suffix.lower() in (".png", ".jpg", ".jpeg")],
            ".webp", existing=all_game_rels)
        clash_ogg = find_suffix_clashes(
            [r for r in all_game_rels
             if Path(r).suffix.lower() in (".wav", ".mp3")],
            ".ogg", existing=all_game_rels)
        for i, name in enumerate(targets, start=1):
            if i % 10 == 1 or i == len(targets):
                p.emit("apk", f"瘦身 {i}/{len(targets)}：{name}")
            f = work / name
            # 审核修复（中-11）：双保险，文件不在直接跳过
            if not f.exists():
                continue
            ext = f.suffix.lower()
            size_before = f.stat().st_size
            game_rel = apk_entry_to_game_rel(name)
            # 实验性：图片转 WebP / 音频转 OGG + 运行时重映射（不改引用，安全）
            if (remap_usable and game_rel
                    and size_before >= preset.min_size_kb * 1024):
                new_suffix = None
                if kind_of(ext) == AssetKind.IMAGE \
                        and ext in (".png", ".jpg", ".jpeg"):
                    new_suffix = ".webp"
                elif kind_of(ext) == AssetKind.AUDIO \
                        and ext in (".wav", ".mp3"):
                    new_suffix = ".ogg"
                if new_suffix:
                    new_rel = Path(game_rel).with_suffix(new_suffix).as_posix()
                    clash = clash_webp if new_suffix == ".webp" else clash_ogg
                    if new_rel in clash:
                        warnings.append(f"{name}：换格式目标与另一资源撞名，"
                                        "已降级为同名压缩。")
                        new_suffix = None
                if new_suffix:
                    new_rel = Path(game_rel).with_suffix(new_suffix).as_posix()
                    new_entry = game_rel_to_apk_entry(new_rel)
                    new_local = work / new_entry
                    new_local.parent.mkdir(parents=True, exist_ok=True)
                    res = None
                    try:
                        if new_suffix == ".webp":
                            res = optimize_image(str(f), str(new_local),
                                                 preset.image_quality,
                                                 convert_webp=True)
                        else:
                            res = convert_audio(str(f), str(new_local),
                                                preset.audio_bitrate_k)
                    except Exception as e:
                        warnings.append(f"{name}：换格式失败，保留原样（{e}）")
                    if res:
                        pending_remap.append((name, new_entry, new_local,
                                              size_before - res["new_size"]))
                        continue
                    # 转换失败落到同名压缩
            res = None
            try:
                if kind_of(ext) == AssetKind.IMAGE and ext != ".gif" \
                        and size_before >= preset.min_size_kb * 1024:
                    res = optimize_image(str(f), str(f), preset.image_quality)
                elif kind_of(ext) == AssetKind.AUDIO and ext in (".ogg", ".mp3"):
                    # 审核补漏：MP3 以前被漏掉（成品侧早已支持），同名
                    # 同格式降码率重编码，APK 里引用焊死也无妨
                    res = reencode_audio(str(f), str(f), preset.audio_bitrate_k)
                elif kind_of(ext) == AssetKind.FONT and ext in (".ttf", ".otf") \
                        and size_before >= 256 * 1024:
                    res = subset_font(str(f), str(f), chars)
            except Exception as e:
                warnings.append(f"{name}：优化失败，保留原样（{e}）")
            if res:
                gained = size_before - f.stat().st_size
                if gained > 0:
                    saved += gained
                    changes += 1
                    changed.add(name)

        # 3.5 重映射落地：编译脚本成功才生效，失败则原图原样保留
        converted: set[str] = set()
        new_entries: dict[str, Path] = {}
        rpyc_entry_name = None
        remap_rpyc: Optional[bytes] = None
        if pending_remap:
            mapping = {}
            for old, new_entry, _local, _g in pending_remap:
                mapping[apk_entry_to_game_rel(old)] = \
                    apk_entry_to_game_rel(new_entry)
            script = remap_mod.build_remap_script(mapping)
            remap_rpyc = compile_remap_rpyc(script, sdk)
        if pending_remap and remap_rpyc:
            for old, new_entry, local, gained in pending_remap:
                converted.add(old)
                new_entries[new_entry] = local
                saved += gained
                changes += 1
            rpyc_entry_name = game_rel_to_apk_entry(
                remap_mod.REMAP_SCRIPT_NAME + "c")
            p.emit("apk", f"已注入重映射脚本，{len(pending_remap)} 个资源"
                          "将在运行时透明换格式（实验性）")
            warnings.append(
                f"实验性功能：{len(pending_remap)} 个资源已换格式（图→WebP、"
                "音→OGG）并注入运行时重映射脚本"
                "（assets/x-game/x-scripts/x-rtools_remap.rpyc）。"
                "若游戏异常，用未开启该开关的版本重跑即可还原。")
        elif pending_remap:
            warnings.append(f"重映射脚本编译失败，{len(pending_remap)} 个资源"
                            "未转换，已原样保留。")

        # 4. 重打包：未改动条目逐字节保留，改动条目用新内容
        p.emit("apk", f"重新打包（替换 {changes} 个条目）……")
        out_apk = work / "slim-unsigned.apk"
        seen_norm: dict[str, str] = {}   # 归一化名（casefold）→ 首次出现的条目名
        with zipfile.ZipFile(str(out_apk), "w") as out_zf:
            for info in zf.infolist():
                if info.filename.endswith("/"):
                    continue
                # 撞名检测：大小写不同或重复的条目名在部分文件系统上会互覆，
                # 仅告警不阻断（行为保持现状）
                norm = info.filename.casefold()
                prev = seen_norm.get(norm)
                if prev is not None:
                    logger.warning("APK 内条目撞名（大小写不敏感）：%r 与 %r",
                                   prev, info.filename)
                    warnings.append(
                        f"包内条目撞名（忽略大小写）：{prev} 与 {info.filename}，"
                        "解压到不区分大小写的文件系统时可能互覆。")
                else:
                    seen_norm[norm] = info.filename
                # 原签名一律作废（内容变了签名必然失效）
                if info.filename.startswith("META-INF/") and \
                        info.filename.upper().endswith(SIGNATURE_SUFFIXES + (".MF",)):
                    continue
                # 已转 WebP 的旧图不再入包（请求会被重映射到新文件）
                if info.filename in converted:
                    continue
                if info.filename in changed:
                    data = (work / info.filename).read_bytes()
                    new_info = zipfile.ZipInfo(info.filename,
                                               date_time=info.date_time)
                    new_info.compress_type = zipfile.ZIP_DEFLATED
                    out_zf.writestr(new_info, data)
                else:
                    # 审核修复（中-16）：优先读 work 下已解出的文件，
                    # 消除大 APK 全量二次解压的重复开销
                    if info.filename in extracted_names:
                        data = (work / info.filename).read_bytes()
                    else:
                        data = zf.read(info.filename)
                    out_zf.writestr(info, data)
            # 新增条目：WebP 新图 + 重映射脚本（原清单里没有，循环后追加）
            for new_entry, local in new_entries.items():
                out_zf.writestr(new_entry, local.read_bytes())
            if remap_rpyc and rpyc_entry_name:
                out_zf.writestr(rpyc_entry_name, remap_rpyc)

        # 5. 对齐
        zipalign, apksigner = find_build_tools(sdk) if sdk else (None, None)
        aligned = work / "slim-aligned.apk"
        if zipalign:
            # 审核修复（中-15）：超时/异常捕获后走"未对齐+警告"回退，
            # 不再让整个瘦身失败；returncode 也查（旧版只看文件存在）
            try:
                za_proc = run_quiet([zipalign, "-f", "4", str(out_apk),
                                     str(aligned)],
                                    capture_output=True, timeout=600)
                if za_proc.returncode != 0:
                    aligned.unlink(missing_ok=True)
            except Exception:
                aligned.unlink(missing_ok=True)
        if not aligned.exists():
            aligned = out_apk
            if zipalign:
                warnings.append("zipalign 对齐失败，产出未对齐的包（可安装，性能略差）。")

        # 6. 签名（三种姿势）
        # 输出目录含非 ASCII（如乱码用户名）时改走短路径；
        # final 保留原路径作展示/返回值，实际写入走 final_real。
        # 注：短路径只对已存在的目录有效，故兜底的是父目录而非文件本身。
        final = apk.parent / (apk.stem + "-瘦身" + apk.suffix)
        final_real = _short_path_fallback(apk.parent, "输出目录", warnings) \
            / final.name
        signed = False
        keystore_info = None
        use_ks, use_pass = keystore, ks_pass
        use_alias = key_alias
        if not (use_ks and use_pass) and generate_key:
            p.emit("apk", "正在生成新的签名钥匙……")
            keystore_info = generate_keystore(str(apk.parent),
                                              password=new_key_password)
            use_ks = keystore_info["keystore"]
            use_pass = keystore_info["password"]
            use_alias = use_alias or keystore_info["alias"]
        if apksigner and use_ks and use_pass:
            # keystore 路径含非 ASCII 时，apksigner(Java) 可能报 Bad pathname，
            # 先复制为 work 内的纯英文临时名再用，结束时随 work 一并清理
            if _has_non_ascii(use_ks):
                ks_tmp = work / "renpyslim_sign.keystore"
                try:
                    shutil.copyfile(use_ks, ks_tmp)
                    logger.info("keystore 路径含非 ASCII 字符，已改用工作目录"
                                "内的纯英文临时副本：%s", ks_tmp)
                    use_ks = str(ks_tmp)
                except OSError as e:
                    logger.warning("keystore 路径含非 ASCII 字符，纯英文临时"
                                   "副本创建失败（%s），按原路径尝试。", e)
                    warnings.append(f"钥匙路径含非 ASCII 字符，临时副本创建失败"
                                    f"（{e}），按原路径尝试签名。")
            # apksigner(Java) 对含乱码/生僻字符的输出路径会报 Bad pathname，
            # 先签到纯英文临时路径再落位，稳
            signer_cmd = _apksigner_cmd(apksigner)
            via_bat = signer_cmd[0] == apksigner
            secret = (use_pass or "") + (key_pass or "")
            if via_bat and any(c in _CMD_DANGEROUS for c in secret):
                # 审核修复（中-14）：回退 .bat 且密码含 cmd 特殊字符，
                # 有拆参/注入风险，拒签并给出人话指引
                warnings.append(
                    "密码含有签名工具无法安全处理的特殊字符，已跳过签名。"
                    "安装 JDK（java 命令可用）后会自动改用安全通道，"
                    "或换一个不含特殊字符的密码重跑。")
            else:
                tmp_signed = work / "slim-signed.apk"
                cmd = [*signer_cmd, "sign", "--ks", use_ks,
                       "--ks-pass", f"pass:{use_pass}"]
                if use_alias:
                    cmd += ["--ks-key-alias", use_alias]
                if key_pass:
                    cmd += ["--key-pass", f"pass:{key_pass}"]
                cmd += ["--out", str(tmp_signed), str(aligned)]
                try:
                    # 审核修复（中-15）：超时/异常捕获，不再让整个瘦身失败
                    proc = run_quiet(cmd, capture_output=True, timeout=600)
                except Exception as e:
                    proc = None
                    warnings.append(f"签名工具调用异常（{e}），产出未签名的包。")
                if proc is not None and proc.returncode == 0 and tmp_signed.exists():
                    shutil.copyfile(tmp_signed, final_real)
                    signed = True
                    if keystore_info:
                        warnings.append(
                            "已用新生成的钥匙签名。密码在钥匙旁边的备忘文件里，"
                            "请妥善保管；玩家需先卸载旧版再安装本包。")
                else:
                    err = (proc.stderr.decode("utf-8", "replace")[-400:]
                           if proc is not None else "")
                    warnings.append(f"重签名失败（钥匙或密码不对？）：{err}")
        if not signed:
            shutil.copyfile(aligned, final_real)
            warnings.append(
                "产出为未签名 APK（未提供 keystore/密码，或签名失败）。"
                "未签名的 APK 无法直接安装；可加 --gen-key 自动生成新钥匙重跑。")

        return {
            "output": str(final_real),
            "saved_bytes": saved,
            "changes": changes,
            "signed": signed,
            "keystore": keystore_info,
            "warnings": warnings,
        }
    finally:
        # 审核修复：异常路径上句柄也要关，否则 Windows 上原 APK 被锁死
        if zf is not None:
            zf.close()
        shutil.rmtree(work, ignore_errors=True)
