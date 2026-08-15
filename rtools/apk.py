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

import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from . import charset as charset_mod
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


class ApkError(Exception):
    pass


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
              progress: Optional[Progress] = None) -> dict:
    """瘦身一个 APK。返回 {output, saved_bytes, signed, changes, warnings}。"""
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

        # 3. 逐个同名优化
        changed: set[str] = set()
        for i, name in enumerate(targets, start=1):
            if i % 10 == 1 or i == len(targets):
                p.emit("apk", f"瘦身 {i}/{len(targets)}：{name}")
            f = work / name
            ext = f.suffix.lower()
            size_before = f.stat().st_size
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
                if info.filename in changed:
                    data = (work / info.filename).read_bytes()
                    new_info = zipfile.ZipInfo(info.filename,
                                               date_time=info.date_time)
                    new_info.compress_type = zipfile.ZIP_DEFLATED
                    out_zf.writestr(new_info, data)
                else:
                    out_zf.writestr(info, zf.read(info.filename))
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

        # 6. 签名
        final = apk.parent / (apk.stem + "-瘦身" + apk.suffix)
        signed = False
        if apksigner and keystore and ks_pass:
            cmd = [apksigner, "sign", "--ks", keystore,
                   "--ks-pass", f"pass:{ks_pass}"]
            if key_alias:
                cmd += ["--ks-key-alias", key_alias]
            if key_pass:
                cmd += ["--key-pass", f"pass:{key_pass}"]
            cmd += ["--out", str(final), str(aligned)]
            proc = run_quiet(cmd, capture_output=True, timeout=600)
            if proc.returncode == 0:
                signed = True
            else:
                err = proc.stderr.decode("utf-8", "replace")[-400:]
                warnings.append(f"重签名失败（钥匙或密码不对？）：{err}")
        if not signed:
            shutil.copyfile(aligned, final)
            warnings.append(
                "产出为未签名 APK（未提供 keystore/密码，或签名失败）。"
                "未签名的 APK 无法直接安装，请提供签名信息后重跑。")

        return {
            "output": str(final),
            "saved_bytes": saved,
            "changes": changes,
            "signed": signed,
            "warnings": warnings,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
