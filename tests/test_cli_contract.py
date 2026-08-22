"""CLI 参数错误 JSON 契约与死参数清理的回归测试。

契约：结果 JSON 走 stdout 且顶层有 ok；参数错误也不例外
（审核修复：自定义 ArgumentParser 子类重写 error()）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CLI = str(ROOT / "cli.py")

# 子进程路线用 UTF-8 直读原始字节，不经过 PowerShell 管道转码
PY = sys.executable


def _run_cli(*argv) -> subprocess.CompletedProcess:
    return subprocess.run([PY, CLI, *argv], capture_output=True,
                          timeout=120, check=False)


def _stdout_json(proc) -> dict:
    return json.loads(proc.stdout.decode("utf-8"))


@pytest.mark.parametrize("argv", [
    [],                        # 无参数：缺必选子命令
    ["nosuchcmd"],             # 非法子命令
    ["analyze"],               # 缺位置参数
    ["env", "--bogus-flag"],   # 未知选项
    ["full", "x", "--mode", "project"],   # 死参数 --mode 已从 full 移除
    ["analyze", "x", "--work-root", "w"],  # 死参数 --work-root 已从 analyze 移除
    ["optimize", "x", "--mode", "nonsense"],  # choices 外的取值
])
def test_arg_errors_emit_json_on_stdout(argv):
    proc = _run_cli(*argv)
    assert proc.returncode == 1, proc.stderr.decode("utf-8", "replace")
    data = _stdout_json(proc)
    assert data["ok"] is False
    assert data.get("error")
    assert "usage" in data


def test_full_rejects_mode_flag():
    """full 语义上只适用于工程，不再接受 --mode。"""
    proc = _run_cli("full", "whatever", "--mode", "dist")
    assert proc.returncode == 1
    data = _stdout_json(proc)
    assert data["ok"] is False
    assert "--mode" in data["error"]


def _make_archive(tmp_path) -> Path:
    """造一个最小的成品压缩包。"""
    from rtools import archives
    dist = tmp_path / "MyGame-pc"
    (dist / "game").mkdir(parents=True)
    (dist / "game" / "script.rpyc").write_bytes(b"RENPY RPC2")
    zp = tmp_path / "MyGame-pc.zip"
    archives.create_zip(str(dist), str(zp))
    return zp


def test_optimize_archive_forces_dist(tmp_path, monkeypatch, capsys):
    """压缩包输入 + 显式 --mode project：打警告但仍走 dist。"""
    zp = _make_archive(tmp_path)
    work = tmp_path / "work"
    out = tmp_path / "out"

    import cli

    calls = {}

    def fake_project(*a, **kw):
        calls["kind"] = "project"
        return {}

    def fake_dist(path, opts, work_root, output_dir, progress,
                  password=None, cancel=None):
        calls["kind"] = "dist"
        calls["path"] = path
        return {"report": tmp_path / "analysis.json",
                "changelog": tmp_path / "changelog.json"}

    monkeypatch.setattr(cli.pipeline, "run_project", fake_project)
    monkeypatch.setattr(cli.pipeline, "run_dist_smart", fake_dist)

    rc = cli.main(["optimize", str(zp), "--mode", "project",
                   "--work-root", str(work), "--output", str(out)])
    assert rc == 0
    assert calls["kind"] == "dist", "压缩包必须走 dist 通道"
    captured = capsys.readouterr()
    assert "警告" in captured.err, "显式传 --mode project 时应打警告"


def test_optimize_archive_default_dist(tmp_path, monkeypatch):
    """压缩包不传 --mode 时也走 dist，且不打警告。"""
    zp = _make_archive(tmp_path)

    import cli

    calls = {}

    def fake_dist(path, opts, work_root, output_dir, progress,
                  password=None, cancel=None):
        calls["kind"] = "dist"
        return {"report": tmp_path / "analysis.json",
                "changelog": tmp_path / "changelog.json"}

    monkeypatch.setattr(cli.pipeline, "run_project",
                        lambda *a, **kw: calls.setdefault("kind", "project"))
    monkeypatch.setattr(cli.pipeline, "run_dist_smart", fake_dist)

    rc = cli.main(["optimize", str(zp),
                   "--work-root", str(tmp_path / "w2"),
                   "--output", str(tmp_path / "o2")])
    assert rc == 0
    assert calls["kind"] == "dist"


def test_optimize_mode_validation_bad_value(tmp_path, monkeypatch, capsys):
    """非法 --mode 取值走 JSON 错误出口（不进入流水线）。"""
    target = tmp_path / "somegame"
    (target / "game").mkdir(parents=True)

    import cli

    monkeypatch.setattr(cli.pipeline, "run_project",
                        lambda *a, **kw: pytest.fail("不应进入流水线"))
    monkeypatch.setattr(cli.pipeline, "run_dist_smart",
                        lambda *a, **kw: pytest.fail("不应进入流水线"))

    with pytest.raises(SystemExit) as ei:
        cli.main(["optimize", str(target), "--mode", "weird"])
    assert ei.value.code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False
    assert "usage" in data


def _stub_pipeline(monkeypatch, calls, tmp_path):
    """把 run_project / run_dist_smart 换成桩，记录进了哪个通道。"""
    import cli

    def fake_project(*a, **kw):
        calls["kind"] = "project"
        return {"report": tmp_path / "analysis.json",
                "changelog": tmp_path / "changelog.json"}

    def fake_dist(path, opts, work_root, output_dir, progress,
                  password=None, cancel=None):
        calls["kind"] = "dist"
        return {"report": tmp_path / "analysis.json",
                "changelog": tmp_path / "changelog.json"}

    monkeypatch.setattr(cli.pipeline, "run_project", fake_project)
    monkeypatch.setattr(cli.pipeline, "run_dist_smart", fake_dist)


def test_optimize_dir_no_mode_auto_project(tmp_path, monkeypatch):
    """目录输入不传 --mode：有 game/*.rpy 自动判 project。

    回归：旧版 _MODE_UNSET 哨兵是真值，不传 --mode 时 `elif mode_arg:`
    恒真，目录优化 100% 报取值校验错，根本进不了流水线。
    """
    target = tmp_path / "MyGame"
    (target / "game").mkdir(parents=True)
    (target / "game" / "script.rpy").write_text("label start: pass", encoding="utf-8")

    calls: dict = {}
    _stub_pipeline(monkeypatch, calls, tmp_path)

    import cli
    rc = cli.main(["optimize", str(target),
                   "--work-root", str(tmp_path / "w"),
                   "--output", str(tmp_path / "o")])
    assert rc == 0
    assert calls["kind"] == "project", "有 .rpy 源码必须走 run_project"


def test_optimize_dir_no_mode_auto_dist(tmp_path, monkeypatch):
    """目录输入不传 --mode：无 .rpy 源码自动判 dist（run_dist_smart）。"""
    target = tmp_path / "MyGame-pc"
    (target / "game").mkdir(parents=True)
    (target / "game" / "script.rpyc").write_bytes(b"RENPY RPC2")

    calls: dict = {}
    _stub_pipeline(monkeypatch, calls, tmp_path)

    import cli
    rc = cli.main(["optimize", str(target),
                   "--work-root", str(tmp_path / "w"),
                   "--output", str(tmp_path / "o")])
    assert rc == 0
    assert calls["kind"] == "dist", "无源码目录必须走 run_dist_smart"


def test_optimize_dir_explicit_mode_dist(tmp_path, monkeypatch):
    """目录输入显式传 --mode dist：直接生效，不再被哨兵误拦。"""
    target = tmp_path / "MyGame"
    (target / "game").mkdir(parents=True)
    (target / "game" / "script.rpy").write_text("label start: pass", encoding="utf-8")

    calls: dict = {}
    _stub_pipeline(monkeypatch, calls, tmp_path)

    import cli
    rc = cli.main(["optimize", str(target), "--mode", "dist",
                   "--work-root", str(tmp_path / "w"),
                   "--output", str(tmp_path / "o")])
    assert rc == 0
    assert calls["kind"] == "dist"
