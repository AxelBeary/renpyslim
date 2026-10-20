"""CLI history 命令回归：列表、条数上限、清空与参数错误契约。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rtools import jobstore

ROOT = Path(__file__).resolve().parent.parent
CLI = str(ROOT / "cli.py")
PY = sys.executable


def _run(home: Path, *argv: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["RENPYSLIM_HOME"] = str(home)
    return subprocess.run([PY, CLI, *argv], capture_output=True, timeout=120,
                          check=False, cwd=str(ROOT), env=env)


def _stdout_json(proc: subprocess.CompletedProcess) -> dict:
    return json.loads(proc.stdout.decode("utf-8"))


def _seed(home: Path, monkeypatch, count: int) -> None:
    monkeypatch.setenv("RENPYSLIM_HOME", str(home))
    for index in range(count):
        jobstore.record_start(f"j{index}", "optimize", title=f"任务{index}")
    jobstore.record_finish(f"j{count - 1}", "done", {"saved_bytes": 10})


def test_history_empty(tmp_path):
    proc = _run(tmp_path, "history")
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert _stdout_json(proc) == {"ok": True, "jobs": []}


def test_history_lists_newest_with_limit(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, 3)

    proc = _run(tmp_path, "history", "--limit", "2")
    assert proc.returncode == 0
    data = _stdout_json(proc)
    assert data["ok"] is True
    assert [job["id"] for job in data["jobs"]] == ["j2", "j1"]
    assert data["jobs"][0]["summary"] == {"saved_bytes": 10}


def test_history_default_limit_is_twenty(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, 25)

    default = _stdout_json(_run(tmp_path, "history"))
    assert len(default["jobs"]) == 20
    full = _stdout_json(_run(tmp_path, "history", "--limit", "50"))
    assert len(full["jobs"]) == 25


def test_history_clear(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, 3)

    cleared = _stdout_json(_run(tmp_path, "history", "--clear"))
    assert cleared == {"ok": True, "cleared": 3}
    assert _stdout_json(_run(tmp_path, "history")) == {"ok": True, "jobs": []}


@pytest.mark.parametrize("value", ["0", "51", "abc"])
def test_history_invalid_limit_stays_json(tmp_path, value):
    proc = _run(tmp_path, "history", "--limit", value)
    assert proc.returncode == 1
    data = _stdout_json(proc)
    assert data["ok"] is False
    assert "1..50" in data["error"] or "整数" in data["error"]
    assert "usage" in data
