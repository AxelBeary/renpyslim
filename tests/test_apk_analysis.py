"""APK 只读预分析回归：筛选口径、两档预估、只读承诺与失败语义。"""
from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from rtools import apk


def _make_apk(tmp_path: Path, signed: bool = False) -> Path:
    path = tmp_path / ("signed.apk" if signed else "demo.apk")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("assets/x-renpy/engine.bin", b"E" * 1000)
        zf.writestr("assets/x-game/x-images/bg.png", b"P" * 400_000)
        zf.writestr("assets/x-game/x-audio/bgm.wav", b"W" * 200_000)
        zf.writestr("assets/x-game/x-fonts/main.ttf", b"T" * 600_000)
        zf.writestr("assets/x-game/x-scripts/script.rpy",
                    "label start:\n    return\n")
        zf.writestr("META-INF/MANIFEST.MF", b"M" * 10)
        if signed:
            zf.writestr("META-INF/CERT.RSA", b"R" * 10)
    return path


def test_analyze_is_readonly_and_counts_only_optimizable_game_assets(tmp_path):
    path = _make_apk(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    result = apk.analyze_apk(str(path))
    report = result["report"]
    stats = result["apk"]

    assert report.mode == "apk"
    assert report.total_size == 400_000 + 200_000 + 600_000
    assert stats["total_entries"] == 6
    assert stats["optimizable"] == 3
    assert stats["script_count"] == 1
    assert stats["signed"] is False
    assert stats["apk_size"] == path.stat().st_size
    assert stats["has_renpy_layout"] is True
    assert stats["est_saving"] > 0
    assert stats["est_saving_remap"] > stats["est_saving"]
    assert all("x-renpy" not in entry["asset"]["rel"]
               for entry in report.to_dict()["entries"])
    assert any("只读预分析" in warning for warning in report.warnings)
    assert any("未签名" in warning for warning in report.warnings)

    # 只读承诺：原包一个字节都没变
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_analyze_detects_signature_block(tmp_path):
    path = _make_apk(tmp_path, signed=True)
    stats = apk.analyze_apk(str(path))["apk"]
    assert stats["signed"] is True


def test_analyze_rejects_non_apk(tmp_path):
    path = tmp_path / "not.apk.txt"
    path.write_bytes(b"")
    with pytest.raises(apk.ApkError, match="只接受 .apk"):
        apk.analyze_apk(str(path))


def test_analyze_rejects_missing_and_bad_zip(tmp_path):
    with pytest.raises(apk.ApkError, match="不存在"):
        apk.analyze_apk(str(tmp_path / "missing.apk"))

    bad = tmp_path / "bad.apk"
    bad.write_bytes(b"not a zip")
    with pytest.raises(apk.ApkError, match="不是有效的 APK"):
        apk.analyze_apk(str(bad))


def test_analyze_warns_when_apk_lacks_renpy_layout(tmp_path):
    path = tmp_path / "other.apk"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("assets/other/thing.png", b"P" * 10_000)

    report = apk.analyze_apk(str(path))["report"]
    assert any("不像 Ren'Py" in warning for warning in report.warnings)
