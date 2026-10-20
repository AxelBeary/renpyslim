"""任务历史持久化回归：原子写、修剪、损坏容错、跨进程中断修正。"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from rtools import jobstore


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("RENPYSLIM_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def tick_clock(monkeypatch):
    state = [100.0]

    def tick():
        state[0] += 1
        return state[0]

    def advance(seconds):
        state[0] += seconds

    tick.advance = advance
    monkeypatch.setattr(jobstore.time, "time", tick)
    return tick


def _read_store(home: Path) -> dict:
    return json.loads((home / "jobs.json").read_text(encoding="utf-8"))


def test_start_finish_and_title_roundtrip(isolated_home):
    jobstore.record_start("j1", "optimize", title="初值")
    assert jobstore.set_title("j1", "MyGame") is True
    jobstore.record_finish("j1", "done", {"saved_bytes": 123})

    jobs = jobstore.list_jobs()
    assert [job["id"] for job in jobs] == ["j1"]
    job = jobs[0]
    assert job["kind"] == "optimize"
    assert job["title"] == "MyGame"
    assert job["status"] == "done"
    assert job["pid"] == os.getpid()
    assert job["summary"] == {"saved_bytes": 123}
    assert job["error"] is None
    assert job["finished"] is not None
    assert job["heartbeat"] == job["finished"]

    stored = _read_store(isolated_home)
    assert stored["version"] == 1
    assert stored["jobs"][0]["status"] == "done"


def test_newest_first_and_limit(isolated_home, tick_clock):
    jobstore.record_start("old", "optimize")
    jobstore.record_start("new", "slimapk")

    assert [job["id"] for job in jobstore.list_jobs()] == ["new", "old"]
    assert [job["id"] for job in jobstore.list_jobs(limit=1)] == ["new"]


def test_prunes_to_fifty_on_write(isolated_home, tick_clock):
    for index in range(55):
        jobstore.record_start(f"j{index}", "optimize")

    jobs = jobstore.list_jobs(limit=50)
    assert len(jobs) == 50
    assert jobs[0]["id"] == "j54"
    assert jobs[-1]["id"] == "j5"
    assert len(_read_store(isolated_home)["jobs"]) == 50


def test_corrupt_file_is_empty_history_not_crash(isolated_home, caplog):
    (isolated_home / "jobs.json").write_text("{not json", encoding="utf-8")

    assert jobstore.list_jobs() == []
    assert any("损坏" in record.message for record in caplog.records)

    jobstore.record_start("after", "optimize")
    assert _read_store(isolated_home)["jobs"][0]["id"] == "after"


def test_stale_running_job_becomes_interrupted(isolated_home):
    jobstore.record_start("live", "optimize")
    payload = _read_store(isolated_home)
    payload["jobs"].insert(0, {
        "id": "ghost",
        "kind": "full",
        "status": "running",
        "created": 1.0,
        "finished": None,
        "title": "上一进程",
        "pid": 99_999_999,
        "summary": {},
        "error": None,
    })
    (isolated_home / "jobs.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    jobs = {job["id"]: job for job in jobstore.list_jobs()}
    assert jobs["ghost"]["status"] == "interrupted"
    assert jobs["ghost"]["error"] == "工具已退出，任务中断"
    assert jobs["ghost"]["finished"] is not None
    assert jobs["live"]["status"] == "running"

    persisted = {job["id"]: job for job in _read_store(isolated_home)["jobs"]}
    assert persisted["ghost"]["status"] == "interrupted"


def test_other_process_with_fresh_heartbeat_stays_running(isolated_home,
                                                         monkeypatch):
    monkeypatch.setattr(jobstore, "_pid_alive", lambda pid: True)
    jobstore.record_start("other", "optimize", pid=12345)
    before = (isolated_home / "jobs.json").read_bytes()

    jobs = jobstore.list_jobs()

    assert jobs[0]["status"] == "running"
    assert (isolated_home / "jobs.json").read_bytes() == before


def test_stale_heartbeat_becomes_interrupted(isolated_home, tick_clock,
                                             monkeypatch):
    monkeypatch.setattr(jobstore, "_pid_alive", lambda pid: True)
    jobstore.record_start("slow", "optimize", pid=12345)
    tick_clock.advance(jobstore.STALE_SECONDS + 1)

    job = jobstore.list_jobs()[0]

    assert job["status"] == "interrupted"
    persisted = _read_store(isolated_home)["jobs"][0]
    assert persisted["status"] == "interrupted"


def test_dead_pid_becomes_interrupted(isolated_home, monkeypatch):
    monkeypatch.setattr(jobstore, "_pid_alive", lambda pid: False)
    jobstore.record_start("dead", "optimize", pid=12345)

    job = jobstore.list_jobs()[0]

    assert job["status"] == "interrupted"
    assert job["error"] == "工具已退出，任务中断"


def test_heartbeat_throttles_then_writes_on_force(isolated_home, tick_clock,
                                                  monkeypatch):
    jobstore.record_start("j", "optimize")
    writes = []
    original_save = jobstore._save_unlocked

    def counting_save(jobs):
        writes.append([dict(job) for job in jobs])
        original_save(jobs)

    monkeypatch.setattr(jobstore, "_save_unlocked", counting_save)

    assert jobstore.heartbeat("j") is True
    assert jobstore.heartbeat("j") is True
    assert writes == []

    tick_clock.advance(jobstore.HEARTBEAT_MIN_INTERVAL + 1)
    assert jobstore.heartbeat("j") is True
    assert len(writes) == 1
    assert writes[0][0]["heartbeat"] == 110

    assert jobstore.heartbeat("j") is True       # 距上次心跳不足 5 秒
    assert len(writes) == 1
    assert jobstore.heartbeat("j", force=True) is True
    assert len(writes) == 2
    assert writes[1][0]["heartbeat"] == 112
    assert jobstore.heartbeat("missing") is False
    assert len(writes) == 2


def test_old_record_without_heartbeat_uses_created(isolated_home):
    payload = {"version": 1, "jobs": [{
        "id": "old",
        "kind": "optimize",
        "status": "done",
        "created": 123.0,
        "finished": 130.0,
        "title": "",
        "pid": 1,
        "summary": {},
        "error": None,
    }]}
    (isolated_home / "jobs.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert jobstore.list_jobs()[0]["heartbeat"] == 123.0


def test_pid_alive_recognizes_current_process_and_invalid_pid():
    assert jobstore._pid_alive(os.getpid()) is True
    assert jobstore._pid_alive(None) is False
    assert jobstore._pid_alive(0) is False


def test_summary_is_json_safe(isolated_home):
    class Weird:
        def __str__(self):
            return "weird-object"

    jobstore.record_start("j", "optimize")
    jobstore.record_finish("j", "done", {
        "path": Path("a/b"),
        "tags": {"x", "y"},
        "obj": Weird(),
        "nan": float("nan"),
    })

    job = jobstore.list_jobs()[0]
    assert job["summary"]["path"] == str(Path("a/b"))
    assert sorted(job["summary"]["tags"]) == ["x", "y"]
    assert job["summary"]["obj"] == "weird-object"
    assert isinstance(job["summary"]["nan"], str)
    json.dumps(job, allow_nan=False)


def test_atomic_write_leaves_no_tmp(isolated_home):
    jobstore.record_start("j", "optimize")
    jobstore.record_finish("j", "done", {"ok": True})
    assert not list(isolated_home.glob("*.tmp*"))


def test_clear_returns_count_and_empties(isolated_home):
    jobstore.record_start("a", "optimize")
    jobstore.record_start("b", "full")

    assert jobstore.clear() == 2
    assert jobstore.list_jobs() == []
    assert _read_store(isolated_home)["jobs"] == []


def test_finish_without_start_keeps_history(isolated_home):
    jobstore.record_finish("late", "failed", error="boom")
    job = jobstore.list_jobs()[0]
    assert job["id"] == "late"
    assert job["status"] == "failed"
    assert job["error"] == "boom"


def test_concurrent_writes_do_not_lose_records(isolated_home):
    def worker(index):
        jobstore.record_start(f"j{index}", "optimize")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(jobstore.list_jobs(limit=50)) == 10
