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


def test_generate_keystore(tmp_path):
    """钥匙生成：钥匙文件 + 密码备忘都得落地。"""
    import pytest as _pt
    if not apk.find_keytool():
        _pt.skip("本机无 keytool")
    info = apk.generate_keystore(str(tmp_path), password="testpass123")
    assert Path(info["keystore"]).exists()
    assert Path(info["memo"]).exists()
    memo = Path(info["memo"]).read_text(encoding="utf-8")
    assert "testpass123" in memo and info["alias"] in memo
    assert info["password"] == "testpass123"


def test_slim_apk_sign_flow_with_generated_key(tmp_path):
    """生成钥匙参与签名流程：钥匙必须生成并传给签名器。

    注：假 APK 没有 AndroidManifest.xml，apksigner 会拒签（真实 APK 必有清单），
    所以这里只断言钥匙生成与流程走通；真实签名由真实 APK 实测覆盖。
    """
    import pytest as _pt
    if not apk.find_keytool():
        _pt.skip("本机无 keytool")
    sdk = r"E:\renpy"
    if not (Path(sdk) / "rapt" / "Sdk" / "build-tools").is_dir():
        _pt.skip("本机无 Android build-tools")
    apk_path = tmp_path / "fake.apk"
    _make_fake_apk(apk_path)
    result = apk.slim_apk(str(apk_path), "balanced", sdk=sdk,
                           generate_key=True, new_key_password="e2epass456")
    assert result["keystore"] is not None
    assert Path(result["keystore"]["keystore"]).exists()
    assert result["keystore"]["password"] == "e2epass456"
    assert Path(result["output"]).exists()
    assert isinstance(result["signed"], bool)
