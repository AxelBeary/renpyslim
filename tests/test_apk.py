"""APK 瘦身（F1）的回归测试：构造假 APK 验证核心安全行为。"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path

from rtools import apk


def _make_fake_apk(path: Path):
    """造一个结构仿真的 APK：游戏图 + 引擎文件 + 签名。"""
    from PIL import Image
    img = Path(path).parent / "_big.png"
    Image.new("RGB", (300, 300)).save(img, "PNG")
    big_bytes = img.read_bytes()
    with zipfile.ZipFile(str(path), "w") as zf:
        zf.writestr("assets/x-game/x-images/pic.png", big_bytes)
        zf.writestr("assets/x-renpy/x-common/engine.png", b"ENGINE-DATA")
        zf.writestr("META-INF/CERT.SF", b"SIGNATURE")
        zf.writestr("META-INF/MANIFEST.MF", b"MANIFEST")
        zf.writestr("classes.dex", b"DEX")
    img.unlink()


def test_apk_slim_core_safety(tmp_path):
    apk_path = tmp_path / "fake.apk"
    _make_fake_apk(apk_path)

    result = apk.slim_apk(str(apk_path), "balanced", sdk=None)

    out = Path(result["output"])
    assert out.exists()
    assert result["saved_bytes"] >= 0

    za = zipfile.ZipFile(str(out))
    names = za.namelist()
    # 引擎文件逐字节保留
    assert za.read("assets/x-renpy/x-common/engine.png") == b"ENGINE-DATA"
    assert za.read("classes.dex") == b"DEX"
    # 旧签名必须被移除
    assert "META-INF/CERT.SF" not in names
    assert "META-INF/MANIFEST.MF" not in names
    # 游戏图仍在（可能被压缩）
    assert "assets/x-game/x-images/pic.png" in names
    # 未提供签名信息 → 未签名 + 明确警告
    assert result["signed"] is False
    assert any("未签名" in w for w in result["warnings"])


def test_build_tools_lookup_missing_sdk(tmp_path):
    za, signer = apk.find_build_tools(str(tmp_path))
    assert za is None and signer is None
