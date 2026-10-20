"""打包平台校验回归：空/未知平台报错，合法平台不被误伤。

package_project 是 CLI 与 Web 的共同出口，校验必须在最前面——
失败时不得创建目的目录、不得调用 SDK。
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from rtools import packager


class _FakeProc:
    returncode = 0
    stdout = b"done"
    stderr = b""


def _fake_sdk(tmp_path):
    sdk = tmp_path / "sdk"
    sdk.mkdir()
    (sdk / "renpy.exe").write_bytes(b"FAKE")
    return sdk


def _fake_proj(tmp_path):
    proj = tmp_path / "proj"
    (proj / "game").mkdir(parents=True)
    (proj / "game" / "script.rpy").write_text("label start: pass",
                                              encoding="utf-8")
    return proj


def test_known_platforms_cover_packages_and_android():
    assert set(packager.PLATFORM_PACKAGES) <= set(packager.KNOWN_PLATFORMS)
    assert "android" in packager.KNOWN_PLATFORMS


def test_validate_platforms_normalizes_and_keeps_order():
    assert packager.validate_platforms([" pc ", "mac"]) == ["pc", "mac"]
    assert packager.validate_platforms(["android"]) == ["android"]


def test_empty_platforms_rejected_without_side_effects(tmp_path):
    sdk, proj = _fake_sdk(tmp_path), _fake_proj(tmp_path)
    dest = tmp_path / "dest"
    with patch.object(packager, "run_quiet",
                      side_effect=lambda *a, **kw:
                      pytest.fail("校验失败不应调用 SDK")):
        with pytest.raises(ValueError, match="至少选择一个打包平台"):
            packager.package_project(str(sdk), str(proj), [],
                                     destination=str(dest), log=lambda m: None)
    assert not dest.exists()


def test_unknown_platform_rejected_not_silently_filtered(tmp_path):
    sdk, proj = _fake_sdk(tmp_path), _fake_proj(tmp_path)
    dest = tmp_path / "dest"
    with patch.object(packager, "run_quiet",
                      side_effect=lambda *a, **kw:
                      pytest.fail("校验失败不应调用 SDK")):
        with pytest.raises(ValueError,
                           match="未知的打包平台.*可选"):
            packager.package_project(str(sdk), str(proj), ["pc", "switch"],
                                     destination=str(dest), log=lambda m: None)
    assert not dest.exists()


def test_pc_platform_still_packages(tmp_path):
    sdk, proj = _fake_sdk(tmp_path), _fake_proj(tmp_path)
    dest = tmp_path / "dest"
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeProc()

    with patch.object(packager, "run_quiet", side_effect=fake_run):
        result = packager.package_project(str(sdk), str(proj), ["pc"],
                                          destination=str(dest),
                                          log=lambda m: None)
    assert result["built"] == ["pc"]
    assert result["errors"] == []
    assert any("distribute" in cmd for cmd in calls)
    assert any(cmd[cmd.index("--package") + 1] == "pc"
               for cmd in calls if "--package" in cmd)


def test_android_platform_still_packages(tmp_path):
    sdk, proj = _fake_sdk(tmp_path), _fake_proj(tmp_path)
    dest = tmp_path / "dest"
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeProc()

    with patch.object(packager, "run_quiet", side_effect=fake_run):
        result = packager.package_project(str(sdk), str(proj), ["android"],
                                          destination=str(dest),
                                          log=lambda m: None)
    assert result["built"] == ["android"]
    assert result["errors"] == []
    assert any("android_build" in cmd for cmd in calls)


def test_padded_platform_name_not_misjudged(tmp_path):
    """带空白的合法平台经校验归一化后照常打包（不因校验被误伤）。"""
    sdk, proj = _fake_sdk(tmp_path), _fake_proj(tmp_path)
    with patch.object(packager, "run_quiet", return_value=_FakeProc()):
        result = packager.package_project(str(sdk), str(proj), [" pc "],
                                          destination=str(tmp_path / "dest"),
                                          log=lambda m: None)
    assert result["built"] == ["pc"]
    assert result["errors"] == []
