"""APK 瘦身（BACKLOG F1，实验性）。

Ren'Py 安卓版结构：assets/x-game/** 是游戏资源，assets/x-renpy/** 是引擎。
策略（同名同格式，绝不改名）：
- 只压 assets/x-game/ 下的图片/音频/字体；引擎目录一律不碰
- 字体字符集来自 APK 内的编译脚本（x-scripts/x-tl 的 rpyc）+ 保底字符集
- 重打包时未改动的条目逐字节原样保留（含压缩方式），只替换改动项
- 改动后原签名必然失效：删除旧签名；提供了钥匙和密码就用
  Android SDK 的 apksigner 重签，否则产出未签名包并明确警告
"""
from __future__ import annotations

import os
import random
import shutil
import string
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from . import charset as charset_mod
from . import remap as remap_mod
from .audio_optimizer import reencode_audio
from .config import PRESETS, CharsetOptions
from .font_optimizer import subset_font
from .image_optimizer import optimize_image
from .models import AssetKind, Progress, kind_of
from .procutil import run_quiet

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


def find_build_tools(sdk: str) -> tuple[Optional[str], Optional[str]]:
    """在 SDK 的 rapt/Sdk/build-tools 里找 zipalign 和 apksigner。"""
    bt_root = Path(sdk) / "rapt" / "Sdk" / "build-tools"
    if not bt_root.is_dir():
        return None, None
    versions = sorted((d for d in bt_root.iterdir() if d.is_dir()), reverse=True)
    for d in versions:
        za = d / "zipalign.exe"
        signer = d / "apksigner.bat"
        if za.exists() and signer.exists():
            return str(za), str(signer)
    return None, None


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
    """从 APK 里的编译脚本提取字符集（rpyc 字节流宽容解码）。"""
    chars: set[str] = set()
    for p in extract_root.rglob("*"):
        if p.is_file() and p.suffix.lower() in (".rpyc", ".rpymc", ".rpy", ".txt"):
            text = charset_mod.read_rpyc_text(p)
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
              webp_remap: bool = False,
              progress: Optional[Progress] = None) -> dict:
    """瘦身一个 APK。签名三种姿势：
    ① 传 keystore+ks_pass → 用原钥匙（可覆盖安装旧版）
    ② 传自有 keystore+密码 → 用自己的身份签
    ③ generate_key=True → 现场生成新钥匙+密码备忘（新身份，需卸载重装）
    都不传 → 未签名产出 + 警告。
    webp_remap=True（实验性）：图片转 WebP 并注入编译好的重映射脚本（B9 的 APK 版）。
    返回 {output, saved_bytes, changes, signed, keystore, warnings}。
    """
    p = progress or Progress()
    preset = PRESETS.get(preset_name, PRESETS["balanced"])
    cs_opts = charset_opts or CharsetOptions()

    apk = Path(apk_path)
    if not apk.exists():
        raise ApkError(f"APK 不存在：{apk_path}")

    work = Path(tempfile.mkdtemp(prefix="renpyslim_apk_"))
    warnings: list[str] = []
    changes = 0
    saved = 0
    try:
        zf = zipfile.ZipFile(str(apk))
        names = zf.namelist()

        # 1. 提取可优化目标
        targets = []
        for name in names:
            if name.endswith("/"):
                continue
            if any(name.startswith(pre) for pre in UNTOUCHABLE_PREFIXES):
                continue
            if not name.startswith("assets/"):
                continue
            if kind_of(Path(name).suffix.lower()) == AssetKind.OTHER:
                continue
            targets.append(name)
        p.emit("apk", f"APK 共 {len(names)} 个条目，可优化资源 {len(targets)} 个")

        for name in targets:
            out = work / name
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src_f:
                out.write_bytes(src_f.read())

        # 2. 从脚本提取字符集（供字体瘦身）
        chars = _extract_charset_from_apk(work, cs_opts)
        p.emit("apk", f"从 APK 内脚本提取到 {len(chars)} 个字符")

        # 3. 逐个同名优化（开了 webp_remap 则图片先试转 WebP）
        remap_usable = webp_remap and bool(sdk) \
            and (Path(sdk) / "renpy.exe").exists()
        if webp_remap and not remap_usable:
            warnings.append("未找到 Ren'Py SDK，无法编译重映射脚本，"
                            "图片降级为同名压缩。")
        changed: set[str] = set()
        pending_remap = []   # (旧条目, 新条目名, 本地新文件, 节省字节)
        for i, name in enumerate(targets, start=1):
            if i % 10 == 1 or i == len(targets):
                p.emit("apk", f"瘦身 {i}/{len(targets)}：{name}")
            f = work / name
            ext = f.suffix.lower()
            size_before = f.stat().st_size
            game_rel = apk_entry_to_game_rel(name)
            # 实验性：图片转 WebP + 运行时重映射（不改引用，安全）
            if (remap_usable and game_rel
                    and kind_of(ext) == AssetKind.IMAGE
                    and ext in (".png", ".jpg", ".jpeg")
                    and size_before >= preset.min_size_kb * 1024):
                new_rel = Path(game_rel).with_suffix(".webp").as_posix()
                new_entry = game_rel_to_apk_entry(new_rel)
                new_local = work / new_entry
                new_local.parent.mkdir(parents=True, exist_ok=True)
                res = None
                try:
                    res = optimize_image(str(f), str(new_local),
                                         preset.image_quality,
                                         convert_webp=True)
                except Exception as e:
                    warnings.append(f"{name}：转 WebP 失败，保留原样（{e}）")
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
                elif kind_of(ext) == AssetKind.AUDIO and ext == ".ogg":
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
            p.emit("apk", f"已注入重映射脚本，{len(pending_remap)} 张图"
                          "将在运行时透明转为 WebP（实验性）")
            warnings.append(
                f"实验性功能：{len(pending_remap)} 张图已转 WebP 并注入运行时"
                "重映射脚本（assets/x-game/x-scripts/x-rtools_remap.rpyc）。"
                "若游戏异常，用未开启该开关的版本重跑即可还原。")
        elif pending_remap:
            warnings.append(f"重映射脚本编译失败，{len(pending_remap)} 张图"
                            "未转换，已原样保留。")

        # 4. 重打包：未改动条目逐字节保留，改动条目用新内容
        p.emit("apk", f"重新打包（替换 {changes} 个条目）……")
        out_apk = work / "slim-unsigned.apk"
        with zipfile.ZipFile(str(out_apk), "w") as out_zf:
            for info in zf.infolist():
                if info.filename.endswith("/"):
                    continue
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
                    out_zf.writestr(info, zf.read(info.filename))
            # 新增条目：WebP 新图 + 重映射脚本（原清单里没有，循环后追加）
            for new_entry, local in new_entries.items():
                out_zf.writestr(new_entry, local.read_bytes())
            if remap_rpyc and rpyc_entry_name:
                out_zf.writestr(rpyc_entry_name, remap_rpyc)
        zf.close()

        # 5. 对齐
        zipalign, apksigner = find_build_tools(sdk) if sdk else (None, None)
        aligned = work / "slim-aligned.apk"
        if zipalign:
            run_quiet([zipalign, "-f", "4", str(out_apk), str(aligned)],
                      capture_output=True, timeout=600)
        if not aligned.exists():
            aligned = out_apk
            if zipalign:
                warnings.append("zipalign 对齐失败，产出未对齐的包（可安装，性能略差）。")

        # 6. 签名（三种姿势）
        final = apk.parent / (apk.stem + "-瘦身" + apk.suffix)
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
            # apksigner(Java) 对含乱码/生僻字符的输出路径会报 Bad pathname，
            # 先签到纯英文临时路径再落位，稳
            tmp_signed = work / "slim-signed.apk"
            cmd = [apksigner, "sign", "--ks", use_ks,
                   "--ks-pass", f"pass:{use_pass}"]
            if use_alias:
                cmd += ["--ks-key-alias", use_alias]
            if key_pass:
                cmd += ["--key-pass", f"pass:{key_pass}"]
            cmd += ["--out", str(tmp_signed), str(aligned)]
            proc = run_quiet(cmd, capture_output=True, timeout=600)
            if proc.returncode == 0 and tmp_signed.exists():
                shutil.copyfile(tmp_signed, final)
                signed = True
                if keystore_info:
                    warnings.append(
                        "已用新生成的钥匙签名。密码在钥匙旁边的备忘文件里，"
                        "请妥善保管；玩家需先卸载旧版再安装本包。")
            else:
                err = proc.stderr.decode("utf-8", "replace")[-400:]
                warnings.append(f"重签名失败（钥匙或密码不对？）：{err}")
        if not signed:
            shutil.copyfile(aligned, final)
            warnings.append(
                "产出为未签名 APK（未提供 keystore/密码，或签名失败）。"
                "未签名的 APK 无法直接安装；可加 --gen-key 自动生成新钥匙重跑。")

        return {
            "output": str(final),
            "saved_bytes": saved,
            "changes": changes,
            "signed": signed,
            "keystore": keystore_info,
            "warnings": warnings,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
