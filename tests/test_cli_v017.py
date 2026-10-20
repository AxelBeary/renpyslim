"""v0.17 CLI 回归：APK 只读预分析、打包平台前置校验、history 不回归。

进程内调用 cli.main（结果 JSON 走 stdout、日志走 stderr 的契约不变），
RENPYSLIM_HOME 由 conftest 夹具隔离到临时目录。
"""
from __future__ import annotations

import json
import zipfile

import pytest


def _make_apk(tmp_path):
    path = tmp_path / "demo.apk"
    with zipfile.ZipFile(str(path), "w") as zf:
        zf.writestr("assets/x-renpy/engine.bin", b"E" * 1000)
        zf.writestr("assets/x-game/x-images/bg.png", b"P" * 400_000)
        zf.writestr("assets/x-game/x-scripts/script.rpy",
                    "label start:\n    return\n")
    return path


def _make_project(tmp_path):
    project = tmp_path / "MyGame"
    (project / "game").mkdir(parents=True)
    (project / "game" / "script.rpy").write_text("label start: pass",
                                                 encoding="utf-8")
    return project


def _stdout_json(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


# ---------------------------------------------------------------------------
# analyze：.apk 自动识别
# ---------------------------------------------------------------------------

def test_analyze_apk_auto_detected(tmp_path, capsys):
    import cli

    apk_path = _make_apk(tmp_path)
    rc = cli.main(["analyze", str(apk_path)])
    data = _stdout_json(capsys)
    assert rc == 0
    assert data["ok"] is True
    assert data["report"]["mode"] == "apk"
    assert data["apk"]["optimizable"] == 1
    assert data["apk"]["has_renpy_layout"] is True
    assert "result" not in data          # 顶层 report/apk，无 result 包装


@pytest.mark.parametrize("mode", ["project", "dist"])
def test_analyze_apk_ignores_pc_modes_with_warning(tmp_path, capsys, mode):
    import cli

    apk_path = _make_apk(tmp_path)
    rc = cli.main(["analyze", str(apk_path), "--mode", mode])
    captured = capsys.readouterr()
    assert rc == 0
    data = json.loads(captured.out)
    assert data["report"]["mode"] == "apk"
    assert "警告" in captured.err
    assert "apk" in captured.err


def test_analyze_apk_mode_explicit(tmp_path, capsys):
    import cli

    apk_path = _make_apk(tmp_path)
    rc = cli.main(["analyze", str(apk_path), "--mode", "apk"])
    captured = capsys.readouterr()
    assert rc == 0
    assert json.loads(captured.out)["report"]["mode"] == "apk"
    assert "警告" not in captured.err


def test_analyze_mode_apk_rejects_non_apk(tmp_path, capsys):
    import cli

    project = _make_project(tmp_path)
    rc = cli.main(["analyze", str(project), "--mode", "apk"])
    data = _stdout_json(capsys)
    assert rc == 1
    assert data["ok"] is False
    assert "apk" in data["error"]


# ---------------------------------------------------------------------------
# full / package：平台校验必须先于流水线与工作目录
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, needle", [
    ("", "至少选择一个"),
    ("switch", "未知的打包平台"),
])
def test_full_invalid_platforms_fail_before_workdir(tmp_path, monkeypatch,
                                                    capsys, value, needle):
    import cli

    project = _make_project(tmp_path)
    monkeypatch.setattr(cli.pipeline, "run_project",
                        lambda *a, **kw: pytest.fail("非法平台不应进入优化"))
    rc = cli.main(["full", str(project), "--platforms", value])
    data = _stdout_json(capsys)
    assert rc == 1
    assert data["ok"] is False
    assert needle in data["error"]
    assert not (tmp_path / "_rtools_work").exists()
    assert not (tmp_path / "_rtools_output").exists()


@pytest.mark.parametrize("value, needle", [
    ("", "至少选择一个"),
    ("switch", "未知的打包平台"),
])
def test_package_invalid_platforms_fail_before_sdk(tmp_path, monkeypatch,
                                                   capsys, value, needle):
    import cli

    monkeypatch.setattr(cli.packager, "find_sdk",
                        lambda *a, **kw: pytest.fail("非法平台不应查找 SDK"))
    rc = cli.main(["package", str(tmp_path / "whatever"),
                   "--platforms", value])
    data = _stdout_json(capsys)
    assert rc == 1
    assert data["ok"] is False
    assert needle in data["error"]


# ---------------------------------------------------------------------------
# history：既有命令不回归
# ---------------------------------------------------------------------------

def test_history_command_still_works(tmp_path, capsys):
    from rtools import jobstore
    import cli

    jobstore.record_start("j1", "optimize", title="任务一")
    jobstore.record_finish("j1", "done", {"saved_bytes": 5})

    rc = cli.main(["history"])
    assert rc == 0
    data = _stdout_json(capsys)
    assert data["ok"] is True
    assert data["jobs"][0]["id"] == "j1"
    assert data["jobs"][0]["summary"] == {"saved_bytes": 5}

    rc = cli.main(["history", "--clear"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "cleared": 1}
