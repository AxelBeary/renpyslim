"""第四批（F2/F3/F4/F6 + 成品线并行）的回归测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from rtools import crashdump, scanner, updater
from rtools.models import Progress
from rtools.pipeline import PipelineCancelled, _run_jobs


def test_updater_version_norm():
    assert updater._norm("v0.9.0") == (0, 9, 0)
    assert updater._norm("V1.2.3") == (1, 2, 3)
    assert updater._norm("garbage") == (0,)
    assert updater._norm("v0.10.0") > updater._norm("v0.9.9")


def test_updater_check_never_raises(monkeypatch):
    # 断网/异常场景必须静默返回 None，绝不抛错打扰用户
    def boom(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(updater.urllib.request, "urlopen", boom)
    assert updater.check_update() is None


def test_crashdump_writes_and_prunes(tmp_path, monkeypatch):
    monkeypatch.setattr(crashdump, "CRASH_DIR", tmp_path / "crashes")
    try:
        raise ValueError("boom")
    except ValueError:
        path = crashdump.write_crash("test-ctx")
    assert path and Path(path).exists()
    assert "ValueError: boom" in Path(path).read_text(encoding="utf-8")


def test_scan_cancel(tmp_path):
    for i in range(5):
        (tmp_path / f"img{i}.png").write_bytes(b"x")
    with pytest.raises(scanner.ScanCancelled):
        scanner.scan_assets(str(tmp_path), probe=False, cancel=lambda: True)


def test_run_jobs_cancel_preserves_done(tmp_path):
    """取消时抛 PipelineCancelled，已完成的结果保留在 partial_results。"""
    jobs = []
    for i in range(6):
        def job():
            return {"saved": 100, "records": []}
        jobs.append((f"j{i}", job))

    # 第一次轮询间隙就喊停（第二波短超时轮询下取消秒级生效）
    with pytest.raises(PipelineCancelled):
        _run_jobs(Progress(), "test", jobs, lambda: True)


def test_run_jobs_cancel_midway_preserves_done(tmp_path):
    """中途取消：已完成的成果随异常带出，不会丢账。"""
    import time
    done_count = {"n": 0}

    def quick():
        done_count["n"] += 1
        return {"saved": 100, "records": []}

    def slow():
        # 用可被杀的子进程拖住（模拟真实长任务）
        import sys

        from rtools import procutil
        # 被杀后 run_quiet 正常返回（返回码非 0），无需吞异常
        procutil.run_quiet([sys.executable, "-c",
                            "import time; time.sleep(60)"], timeout=120)
        return {"saved": 999, "records": []}

    jobs = [(f"q{i}", quick) for i in range(4)] + [("slow", slow)]
    started = time.monotonic()
    with pytest.raises(PipelineCancelled) as ei:
        _run_jobs(Progress(), "test", jobs,
                  lambda: done_count["n"] >= 4
                  and time.monotonic() - started > 0.3)
    # 快任务的成果全部保留在 partial_results 里
    assert sum(r.get("saved", 0) for r in ei.value.partial_results) >= 400
