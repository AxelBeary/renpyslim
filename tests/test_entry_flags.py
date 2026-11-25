"""GUI 入口的参数分流：窗口版 exe 被当命令行用时不许乱来。

回归背景（v0.17.2 实测）：exe 的入口是 main.py，它把 `sys.argv[1]` 直接当
"要打开的工程路径"。于是 `RenPySlim.exe --version` 会把 `--version` 填进路径框、
起本地服务、还打开浏览器标签，而且因为 console=False 什么都打不出来——cli 用户
既拿不到版本号，也退不出来。v0.17.3 起入口先分流：信息开关与子命令走无头 CLI，
未知选项直接报错退出，只有真·路径才进界面。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _run(args, tmp_path, timeout=120):
    """跑 main.py 子进程；HOME 指到临时目录，绝不碰用户真实的 ~/.renpytools。"""
    env = dict(os.environ)
    for key in ("USERPROFILE", "HOME", "RENPYSLIM_HOME"):
        env[key] = str(tmp_path)
    return subprocess.run(
        [sys.executable, str(ROOT / "main.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
        cwd=str(ROOT),
    )


@pytest.mark.parametrize("arg", ["--version", "-V"])
def test_version_flag_prints_version_and_does_not_start_gui(arg, tmp_path):
    r = _run([arg], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "renpyslim" in r.stdout.lower()
    # 关键：没有起服务，也就没有登记端口文件
    assert not (tmp_path / ".renpytools" / "runtime.json").exists()


def test_help_flag_shows_usage_without_starting_gui(tmp_path):
    r = _run(["--help"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "usage:" in r.stdout.lower()
    assert not (tmp_path / ".renpytools" / "runtime.json").exists()


def test_cli_subcommand_is_routed_to_headless_cli(tmp_path):
    r = _run(["env"], tmp_path)
    assert r.returncode == 0, r.stderr
    # 无头 CLI 的 stdout 契约：纯 JSON
    import json

    data = json.loads(r.stdout)
    assert data["ok"] is True
    assert not (tmp_path / ".renpytools" / "runtime.json").exists()


def test_unknown_option_is_rejected_not_treated_as_path(tmp_path):
    r = _run(["--definitely-not-a-flag"], tmp_path)
    assert r.returncode == 2
    # ASCII 断言是行为契约；中文断言同时验证"管道里是 UTF-8 而不是 \uXXXX 转义"
    # （CI runner 的默认管道编码是 cp1252，曾把这里的中文打成反斜杠转义）
    assert "--definitely-not-a-flag" in r.stderr
    assert "未知选项" in r.stderr
    assert not (tmp_path / ".renpytools" / "runtime.json").exists()


def test_classify_argv_never_treats_dash_as_path():
    sys.path.insert(0, str(ROOT))
    try:
        import main as entry
    finally:
        sys.path.pop(0)

    assert entry._classify_argv([]) == "gui"
    assert entry._classify_argv([r"C:\games\demo"]) == "gui"
    assert entry._classify_argv(["demo.zip"]) == "gui"
    assert entry._classify_argv(["--version"]) == "cli"
    assert entry._classify_argv(["--help"]) == "cli"
    assert entry._classify_argv(["analyze"]) == "cli"
    assert entry._classify_argv(["--oops"]) == "bad"
    assert entry._classify_argv(["-x"]) == "bad"


def test_spec_builds_both_gui_and_cli_exes():
    """打包配方必须同时产出窗口版与控制台版：CLI 只能靠控制台子系统才稳定输出。"""
    spec = (ROOT / "RenPySlim.spec").read_text(encoding="utf-8")
    assert "name='RenPySlim'" in spec
    assert "name='RenPySlim-cli'" in spec, "控制台版 CLI exe 没有出现在打包配方里"
    assert "console=True" in spec, "CLI exe 必须是控制台子系统，否则终端里看不到输出"


def test_release_workflow_uploads_both_exes():
    """发版流水线两个产物都要传，否则用户下载不到能用的 CLI。"""
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "RenPySlim.exe" in workflow
    assert "RenPySlim-cli.exe" in workflow, "release.yml 没有上传控制台版 CLI exe"


@pytest.mark.parametrize("value", ["70000", "-1", "0"])
def test_pick_port_rejects_out_of_range_values(value, monkeypatch):
    sys.path.insert(0, str(ROOT))
    try:
        import main as entry
    finally:
        sys.path.pop(0)

    monkeypatch.setenv("RENPYTOOLS_PORT", value)
    monkeypatch.setattr(entry, "_port_free", lambda port: port == entry.DEFAULT_PORT)
    assert entry._pick_port() == entry.DEFAULT_PORT


def test_runtime_port_out_of_range_is_ignored(monkeypatch):
    sys.path.insert(0, str(ROOT))
    try:
        import main as entry
    finally:
        sys.path.pop(0)

    assert entry._port_alive(70000) is False
    monkeypatch.setattr(entry.runtime, "read_port", lambda: 70000)
    assert entry._running_instance_port() is None
