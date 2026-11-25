"""CLI 任务写入统一任务历史：analyze/optimize/full/package/slimapk/slimfont。

进程内调用 cli.main，RENPYSLIM_HOME 由 conftest 夹具隔离到临时目录；
不跑真实优化，APK 夹具只造 zip 条目。env / history 是查询类命令，不记账。
"""
from __future__ import annotations

import json
import os
import threading
import zipfile
from types import SimpleNamespace

import pytest

from rtools import jobstore


def _make_apk(tmp_path):
    """只读预分析用的最小 APK：只造 zip 条目，无需真实游戏资源。"""
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
# 成功 / 失败路径都要记账
# ---------------------------------------------------------------------------

def test_analyze_apk_records_done_job(tmp_path, capsys):
    import cli

    apk = _make_apk(tmp_path)
    rc = cli.main(["analyze", str(apk)])
    data = _stdout_json(capsys)
    assert rc == 0
    assert data["ok"] is True                        # 结果 JSON 契约不变

    jobs = jobstore.list_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job["kind"] == "analyze"
    assert job["status"] == "done"
    assert job["title"] == str(apk)
    assert job["pid"] == os.getpid()
    assert job["error"] is None
    # 摘要只留小字段：APK 汇总里的规模指标
    assert job["summary"]["optimizable"] == 1
    assert job["summary"]["apk_size"] > 0
    assert job["summary"]["mode"] == "apk"
    # entries 是条目数而不是明细列表
    assert isinstance(job["summary"]["entries"], int)


def test_failed_analyze_records_failed_job(tmp_path, capsys):
    import cli

    missing = tmp_path / "nope"
    rc = cli.main(["analyze", str(missing)])
    data = _stdout_json(capsys)
    assert rc == 1
    assert data["ok"] is False

    jobs = jobstore.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["kind"] == "analyze"
    assert jobs[0]["status"] == "failed"
    assert "不存在" in jobs[0]["error"]
    assert jobs[0]["summary"] == {}


def test_full_invalid_platforms_records_failed(tmp_path, monkeypatch, capsys):
    """平台校验先于优化：失败任务同样要落历史，且不进流水线。"""
    import cli

    project = _make_project(tmp_path)
    monkeypatch.setattr(cli.pipeline, "run_project",
                        lambda *a, **kw: pytest.fail("非法平台不应进入优化"))
    rc = cli.main(["full", str(project), "--platforms", ""])
    data = _stdout_json(capsys)
    assert rc == 1
    assert data["ok"] is False

    jobs = jobstore.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["kind"] == "full"
    assert jobs[0]["status"] == "failed"
    assert "至少选择一个" in jobs[0]["error"]
    assert jobs[0]["title"] == str(project)


# ---------------------------------------------------------------------------
# env / history 不记账
# ---------------------------------------------------------------------------

def test_history_and_env_do_not_record(tmp_path, monkeypatch, capsys):
    import cli

    jobstore.record_start("seed", "optimize", title="既有任务")
    jobstore.record_finish("seed", "done", {"saved_bytes": 3})

    assert cli.main(["history"]) == 0
    data = _stdout_json(capsys)
    assert data["jobs"][0]["id"] == "seed"
    assert [job["id"] for job in jobstore.list_jobs()] == ["seed"]

    # env 只跑体检不对账，桩掉真实环境探测避免慢与副作用
    monkeypatch.setattr(cli.packager, "check_environment",
                        lambda *a, **kw: {"sdk_path": None})
    assert cli.main(["env"]) == 0
    assert _stdout_json(capsys)["ok"] is True
    assert [job["id"] for job in jobstore.list_jobs()] == ["seed"]


def test_history_clear_empties_cli_jobs(tmp_path, capsys):
    import cli

    apk = _make_apk(tmp_path)
    assert cli.main(["analyze", str(apk)]) == 0
    capsys.readouterr()
    assert len(jobstore.list_jobs()) == 1

    assert cli.main(["history", "--clear"]) == 0
    assert _stdout_json(capsys) == {"ok": True, "cleared": 1}
    assert jobstore.list_jobs() == []


# ---------------------------------------------------------------------------
# 历史写入失败不得影响 CLI 结果，异常路径落 error
# ---------------------------------------------------------------------------

def test_record_start_failure_does_not_break_cli(tmp_path, monkeypatch, capsys):
    import cli

    def _boom(*args, **kwargs):
        raise OSError("磁盘满了")

    monkeypatch.setattr(cli.jobstore, "record_start", _boom)
    apk = _make_apk(tmp_path)
    rc = cli.main(["analyze", str(apk)])
    captured = capsys.readouterr()
    assert rc == 0
    assert json.loads(captured.out)["ok"] is True
    assert "登记任务历史失败" in captured.err
    assert jobstore.list_jobs() == []


def test_record_finish_failure_does_not_break_cli(tmp_path, monkeypatch, capsys):
    import cli

    def _boom(*args, **kwargs):
        raise OSError("写盘失败")

    monkeypatch.setattr(cli.jobstore, "record_finish", _boom)
    apk = _make_apk(tmp_path)
    rc = cli.main(["analyze", str(apk)])
    captured = capsys.readouterr()
    assert rc == 0
    assert json.loads(captured.out)["ok"] is True
    assert "任务历史写入失败" in captured.err


def test_unexpected_exception_records_error(tmp_path, monkeypatch, capsys):
    import cli

    def _boom(args):
        raise RuntimeError("炸了")

    monkeypatch.setattr(cli, "cmd_analyze", _boom)
    monkeypatch.setattr(cli.crashdump, "write_crash", lambda context: "")
    apk = _make_apk(tmp_path)
    rc = cli.main(["analyze", str(apk)])
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert rc == 1
    assert data["ok"] is False
    assert "意外错误" in data["error"]

    jobs = jobstore.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "error"
    assert "RuntimeError" in jobs[0]["error"]


def test_interrupted_job_records_error(tmp_path, monkeypatch):
    """Ctrl+C（BaseException）也要收尾，不能留 running 幽灵记录。"""
    import cli

    def _interrupt(args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "cmd_analyze", _interrupt)
    apk = _make_apk(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        cli.main(["analyze", str(apk)])

    jobs = jobstore.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "error"
    assert "KeyboardInterrupt" in jobs[0]["error"]


# ---------------------------------------------------------------------------
# 长任务心跳：六类任务统一刷新，结束时线程可靠回收
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command,target", [
    ("analyze", "dummy.apk"),
    ("optimize", "dummy"),
    ("full", "dummy"),
    ("package", "dummy"),
    ("slimapk", "dummy.apk"),
    ("slimfont", "dummy.ttf"),
])
def test_all_cli_tasks_heartbeat_while_running(tmp_path, monkeypatch,
                                               capsys, command, target):
    """模拟超过 stale 阈值的工作时长，六类任务都不能被误判 interrupted。"""
    import cli

    clock = [1000.0]
    heartbeat_written = threading.Event()
    monkeypatch.setattr(jobstore, "time",
                        SimpleNamespace(time=lambda: clock[0]))
    monkeypatch.setattr(cli, "_CLI_HEARTBEAT_INTERVAL", 0.01)
    original_heartbeat = jobstore.heartbeat

    def advancing_heartbeat(job_id, force=False):
        # 让每一次真实后台刷新都跨越 180 秒阈值；没有心跳的旧实现必红。
        clock[0] += jobstore.STALE_SECONDS + 1
        result = original_heartbeat(job_id, force=force)
        heartbeat_written.set()
        return result

    monkeypatch.setattr(jobstore, "heartbeat", advancing_heartbeat)
    observed = {}

    def fake_command(args):
        thread = cli._ACTIVE_JOB["heartbeat_thread"]
        observed["thread"] = thread
        assert thread.daemon is True
        assert thread.is_alive() is True
        assert heartbeat_written.wait(timeout=2)
        observed["during"] = jobstore.list_jobs()[0]
        return cli._ok({"simulated": True})

    monkeypatch.setattr(cli, f"cmd_{command}", fake_command)
    rc = cli.main([command, str(tmp_path / target)])
    captured = capsys.readouterr()

    assert rc == 0
    assert json.loads(captured.out) == {"ok": True, "simulated": True}
    assert observed["during"]["kind"] == command
    assert observed["during"]["status"] == "running"
    assert jobstore._pid_alive(observed["during"]["pid"]) is True
    assert observed["thread"].is_alive() is False
    jobs = jobstore.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "done"


@pytest.mark.parametrize("outcome", ["failed", "exception", "cancelled"])
def test_heartbeat_thread_stops_on_every_exit_path(tmp_path, monkeypatch,
                                                  capsys, outcome):
    """_fail、普通异常与 KeyboardInterrupt 取消都要先停线程再写终态。"""
    import cli

    heartbeat_written = threading.Event()
    monkeypatch.setattr(cli, "_CLI_HEARTBEAT_INTERVAL", 0.01)
    original_heartbeat = jobstore.heartbeat

    def signaling_heartbeat(job_id, force=False):
        result = original_heartbeat(job_id, force=force)
        heartbeat_written.set()
        return result

    monkeypatch.setattr(jobstore, "heartbeat", signaling_heartbeat)
    monkeypatch.setattr(cli.crashdump, "write_crash", lambda context: "")
    observed = {}

    def fake_command(args):
        observed["thread"] = cli._ACTIVE_JOB["heartbeat_thread"]
        assert heartbeat_written.wait(timeout=2)
        if outcome == "failed":
            return cli._fail("模拟失败")
        if outcome == "exception":
            raise RuntimeError("模拟异常")
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "cmd_analyze", fake_command)
    argv = ["analyze", str(tmp_path / "dummy.apk")]
    if outcome == "cancelled":
        with pytest.raises(KeyboardInterrupt):
            cli.main(argv)
    else:
        assert cli.main(argv) == 1
    captured = capsys.readouterr()

    assert observed["thread"].is_alive() is False
    jobs = jobstore.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["status"] == ("failed" if outcome == "failed" else "error")
    if outcome == "failed":
        assert json.loads(captured.out) == {"ok": False, "error": "模拟失败"}
    elif outcome == "exception":
        data = json.loads(captured.out)
        assert data["ok"] is False
        assert "RuntimeError" in data["error"]


def test_heartbeat_failure_never_pollutes_stdout(tmp_path, monkeypatch, capsys):
    """后台心跳写盘失败只记 stderr，CLI 结果 JSON 与退出码保持不变。"""
    import cli

    attempted = threading.Event()
    monkeypatch.setattr(cli, "_CLI_HEARTBEAT_INTERVAL", 0.01)

    def broken_heartbeat(job_id, force=False):
        attempted.set()
        raise OSError("心跳写盘失败")

    monkeypatch.setattr(jobstore, "heartbeat", broken_heartbeat)

    def fake_command(args):
        assert attempted.wait(timeout=2)
        return cli._ok({"simulated": True})

    monkeypatch.setattr(cli, "cmd_analyze", fake_command)
    rc = cli.main(["analyze", str(tmp_path / "dummy.apk")])
    captured = capsys.readouterr()

    assert rc == 0
    assert json.loads(captured.out) == {"ok": True, "simulated": True}
    assert "刷新任务心跳失败" in captured.err
    assert cli._ACTIVE_JOB["heartbeat_thread"].is_alive() is False
    assert jobstore.list_jobs()[0]["status"] == "done"


# ---------------------------------------------------------------------------
# 摘要抽取：只留小字段，可 JSON 序列化
# ---------------------------------------------------------------------------

def test_summary_from_ok_flattens_full_and_drops_large_payloads():
    import cli

    data = {
        "optimize": {
            "saved_bytes": 100, "working_dir": "C:/work",
            "report": "C:/out/analysis.json",
            "changelog": "C:/out/changelog.json",
            "changes": [], "warnings": ["只记数量" * 1000] * 50,
        },
        "package": {
            "destination": "C:/out", "built": ["pc", "mac"],
            "errors": [], "log": "刷屏" * 5000,
        },
    }
    summary = cli._summary_from_ok(data)
    assert summary["saved_bytes"] == 100
    assert summary["working_dir"] == "C:/work"
    assert summary["changelog"] == "C:/out/changelog.json"
    assert summary["changes"] == 0
    assert summary["destination"] == "C:/out"
    assert summary["built"] == ["pc", "mac"]
    assert "warnings" not in summary
    assert "errors" not in summary
    assert "log" not in summary
    json.dumps(summary)


def test_summary_from_ok_extracts_slimfont_and_slimapk_fields():
    import cli

    font_summary = cli._summary_from_ok({"result": {
        "font": "demo.ttf", "charset_size": 12,
        "charlist": "字" * 5000,                    # 大列表绝不进历史
        "outputs": [{"out": "C:/out/demo-slim.ttf", "old_size": 100,
                     "new_size": 50}],
        "warnings": ["提示"],
    }})
    assert font_summary["output"] == "C:/out/demo-slim.ttf"
    assert "charlist" not in font_summary
    assert "outputs" not in font_summary

    apk_summary = cli._summary_from_ok({"result": {
        "output": "C:/out/demo.apk", "saved_bytes": 9, "changes": 3,
        "signed": True,
        # 钥匙信息含密码，绝不进历史
        "keystore": {"password": "secret", "path": "C:/k.jks"},
    }})
    assert apk_summary == {"output": "C:/out/demo.apk", "saved_bytes": 9,
                           "changes": 3, "signed": True}
