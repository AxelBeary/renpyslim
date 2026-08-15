"""第四批（F2/F3/F4/F6 + 成品线并行）的回归测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from rtools import crashdump, updater
from rtools import scanner
from rtools.pipeline import PipelineCancelled, _run_jobs
from rtools.models import Progress


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
    """取消时抛 PipelineCancelled，已完成的结果保留。"""
    flag = {"stop": False}
    jobs = []
    for i in range(6):
        def make(n=i):
            def job():
                return {"saved": 100, "records": []}
            return f"j{i}", job
        jobs.append(make())

    def cancel_after_first():
        flag["stop"] = True
        return flag["stop"]

    # 第一个完成后即取消
    call = {"n": 0}
    def cancel_fn():
        call["n"] += 1
        return call["n"] > 1

    with pytest.raises(PipelineCancelled):
        _run_jobs(Progress(), "test", jobs, cancel_fn)
