"""打包注入配置自动清理与产物清单去陈旧的回归测试。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from rtools import packager


def _fake_sdk(tmp_path) -> Path:
    sdk = tmp_path / "sdk"
    sdk.mkdir()
    (sdk / "renpy.exe").write_bytes(b"FAKE")
    return sdk


def _fake_proj(tmp_path) -> Path:
    proj = tmp_path / "proj"
    (proj / "game").mkdir(parents=True)
    (proj / "game" / "script.rpy").write_text("label start: pass")
    return proj


class _FakeProc:
    returncode = 0
    stdout = b"done"
    stderr = b""


def test_injected_config_removed_after_success(tmp_path):
    sdk, proj = _fake_sdk(tmp_path), _fake_proj(tmp_path)
    cfg = proj / "game" / packager.ARCHIVE_CONFIG_NAME

    with patch.object(packager, "run_quiet", return_value=_FakeProc()):
        packager.package_project(str(sdk), str(proj), ["pc"],
                                 destination=str(tmp_path / "dest"),
                                 log=lambda m: None, archive_rpa=True)
    assert not cfg.exists(), "打包结束后注入的归档配置必须被删除"


def test_injected_config_removed_on_sdk_failure(tmp_path):
    """SDK 命令失败也得清理。"""
    sdk, proj = _fake_sdk(tmp_path), _fake_proj(tmp_path)
    cfg = proj / "game" / packager.ARCHIVE_CONFIG_NAME

    bad = _FakeProc()
    bad.returncode = 1
    with patch.object(packager, "run_quiet", return_value=bad):
        result = packager.package_project(str(sdk), str(proj), ["pc"],
                                          destination=str(tmp_path / "dest"),
                                          log=lambda m: None, archive_rpa=True)
    assert result["errors"], "SDK 失败应记录错误"
    assert not cfg.exists()


def test_injected_config_removed_on_exception(tmp_path):
    """SDK 命令抛异常（如超时）也得清理（finally 语义）。"""
    sdk, proj = _fake_sdk(tmp_path), _fake_proj(tmp_path)
    cfg = proj / "game" / packager.ARCHIVE_CONFIG_NAME

    with patch.object(packager, "run_quiet",
                      side_effect=subprocess.TimeoutExpired("renpy", 3600)):
        packager.package_project(str(sdk), str(proj), ["pc"],
                                 destination=str(tmp_path / "dest"),
                                 log=lambda m: None, archive_rpa=True)
    assert not cfg.exists()


def test_config_present_during_sdk_call(tmp_path):
    """清理必须发生在 SDK 命令执行之后：命令跑的时候配置要在场。"""
    sdk, proj = _fake_sdk(tmp_path), _fake_proj(tmp_path)
    cfg = proj / "game" / packager.ARCHIVE_CONFIG_NAME
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["exists"] = cfg.exists()
        return _FakeProc()

    with patch.object(packager, "run_quiet", side_effect=fake_run):
        packager.package_project(str(sdk), str(proj), ["pc"],
                                 destination=str(tmp_path / "dest"),
                                 log=lambda m: None, archive_rpa=True)
    assert seen["exists"] is True, "SDK 打包时注入的归档配置必须在场"
    assert not cfg.exists()


def test_artifacts_exclude_stale_files(tmp_path):
    """目的目录里上次残留的旧包不计入本次产物清单。"""
    sdk, proj = _fake_sdk(tmp_path), _fake_proj(tmp_path)
    dest = tmp_path / "dest"
    dest.mkdir()
    stale = dest / "old-game-pc.zip"
    stale.write_bytes(b"stale")
    os.utime(stale, (1000000, 1000000))   # 远古时间，杜绝时钟边界巧合

    def fake_run(cmd, **kwargs):
        (dest / "new-game-pc.zip").write_bytes(b"fresh")
        return _FakeProc()

    with patch.object(packager, "run_quiet", side_effect=fake_run):
        result = packager.package_project(str(sdk), str(proj), ["pc"],
                                          destination=str(dest),
                                          log=lambda m: None)
    names = [a["name"] for a in result["artifacts"]]
    assert names == ["new-game-pc.zip"], "只应报告本次新出现的产物"


def test_artifacts_include_updated_same_name(tmp_path):
    """同名旧包被本次打包覆盖（mtime 更新）时应计入。"""
    sdk, proj = _fake_sdk(tmp_path), _fake_proj(tmp_path)
    dest = tmp_path / "dest"
    dest.mkdir()
    old = dest / "game-pc.zip"
    old.write_bytes(b"v1")
    os.utime(old, (1000000, 1000000))

    def fake_run(cmd, **kwargs):
        old.write_bytes(b"v2-new-content")
        os.utime(old, None)   # 刷新为当前时间
        return _FakeProc()

    with patch.object(packager, "run_quiet", side_effect=fake_run):
        result = packager.package_project(str(sdk), str(proj), ["pc"],
                                          destination=str(dest),
                                          log=lambda m: None)
    names = [a["name"] for a in result["artifacts"]]
    assert names == ["game-pc.zip"]
