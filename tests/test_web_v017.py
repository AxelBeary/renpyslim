"""v0.17 Web 接口回归：任务历史、安全打开目录、平台校验、APK 预分析、结构化日志。

全部用例走 TestClient(127.0.0.1) 过“本地专用”防护，不启真实服务、
不碰真实游戏目录；任务历史由 conftest 的 RENPYSLIM_HOME 夹具隔离。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import zipfile
from pathlib import Path

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None

import web.app as app_mod
from rtools import jobstore

pytestmark = pytest.mark.skipif(TestClient is None, reason="缺少测试客户端依赖")

BASE_URL = "http://127.0.0.1:52786"


@pytest.fixture()
def client():
    # base_url 指定本机门牌号，避免被“本地专用”防护拦下
    with TestClient(app_mod.app, base_url=BASE_URL) as c:
        yield c


def _reset_state() -> None:
    with app_mod._QUEUE_LOCK:
        app_mod._QUEUE.clear()
        app_mod._JOB_TASKS.clear()
        app_mod._RUNNING["id"] = None
    with app_mod.JOBS_LOCK:
        app_mod.JOBS.clear()


@pytest.fixture(autouse=True)
def clean_state():
    _reset_state()
    yield
    _reset_state()


def _wait_job(client, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    data: dict = {}
    while time.time() < deadline:
        data = client.get(f"/api/job/{job_id}").json()
        if data["status"] in ("done", "error", "canceled"):
            return data
        time.sleep(0.02)
    pytest.fail(f"任务未在 {timeout} 秒内结束：{data}")


def _make_apk(tmp_path) -> Path:
    path = tmp_path / "demo.apk"
    with zipfile.ZipFile(str(path), "w") as zf:
        zf.writestr("assets/x-renpy/engine.bin", b"E" * 1000)
        zf.writestr("assets/x-game/x-images/bg.png", b"P" * 400_000)
        zf.writestr("assets/x-game/x-scripts/script.rpy",
                    "label start:\n    return\n")
        zf.writestr("META-INF/MANIFEST.MF", b"M" * 10)
    return path


# ---------------------------------------------------------------------------
# 任务历史
# ---------------------------------------------------------------------------

def test_history_lists_newest_and_clears(client):
    jobstore.record_start("old", "optimize", title="旧任务")
    jobstore.record_finish("old", "done", {"saved_bytes": 10})
    jobstore.record_start("new", "slimapk", title="新任务")
    jobstore.record_finish("new", "error", error="boom")

    data = client.get("/api/history?limit=1").json()
    assert data["ok"] is True
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["id"] == "new"
    assert data["jobs"][0]["title"] == "新任务"
    assert data["jobs"][0]["status"] == "error"
    assert data["jobs"][0]["error"] == "boom"

    everything = client.get("/api/history").json()
    assert [job["id"] for job in everything["jobs"]] == ["new", "old"]
    assert everything["jobs"][1]["summary"] == {"saved_bytes": 10}

    cleared = client.delete("/api/history").json()
    assert cleared == {"ok": True, "cleared": 2}
    assert client.get("/api/history").json() == {"ok": True, "jobs": []}


def test_history_summary_keeps_only_small_fields():
    """历史摘要只留小字段：超长 charlist、完整报告绝不进历史。"""
    result = {
        "mode": "project",
        "working_dir": "E:/work/copy",
        "saved_bytes": 123,
        "changelog": "E:/out/changelog.json",
        "report": "E:/out/analysis.json",
        "report_dict": {"entries": ["x" * 1000]},
        "charlist": "字" * 5000,
        "changes": [{"action": "compress"}] * 3,
    }
    summary = app_mod._history_summary("optimize", result)
    assert summary["saved_bytes"] == 123
    assert summary["working_dir"] == "E:/work/copy"
    assert summary["changelog"] == "E:/out/changelog.json"
    assert summary["changes"] == 3
    assert "report" not in summary and "report_dict" not in summary
    assert "charlist" not in summary          # 超长字符集不塞历史
    json.dumps(summary, allow_nan=False)

    full = {
        "optimize": {"working_dir": "E:/work/copy", "saved_bytes": 5},
        "package": {"built": ["pc"], "destination": "E:/dist",
                    "errors": ["oops"]},
    }
    flat = app_mod._history_summary("full", full)
    assert flat["working_dir"] == "E:/work/copy"
    assert flat["saved_bytes"] == 5
    assert flat["built"] == ["pc"]
    assert flat["destination"] == "E:/dist"
    assert flat["errors"] == 1
    json.dumps(flat, allow_nan=False)


# ---------------------------------------------------------------------------
# 打开目录 / 定位文件
# ---------------------------------------------------------------------------

def test_reveal_directory_and_file(client, tmp_path, monkeypatch):
    calls = []

    def fake_open(target, *, select):
        calls.append((target, select))

    monkeypatch.setattr(app_mod, "_open_in_explorer", fake_open)

    folder = tmp_path / "output"
    folder.mkdir()
    assert client.post("/api/reveal",
                       json={"path": str(folder)}).json() == {"ok": True}
    assert calls[-1] == (folder, False)

    target = folder / "game-pc.zip"
    target.write_bytes(b"ZIP")
    r = client.post("/api/reveal", json={"path": str(target), "reveal": True})
    assert r.json() == {"ok": True}
    assert calls[-1] == (target, True)

    # 文件但不要定位：打开所在目录，绝不执行文件
    r = client.post("/api/reveal", json={"path": str(target)})
    assert r.json() == {"ok": True}
    assert calls[-1] == (folder, False)


def test_reveal_rejects_missing_relative_and_unc(client, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(app_mod, "_open_in_explorer",
                        lambda target, *, select: calls.append((target, select)))

    missing = client.post("/api/reveal",
                          json={"path": str(tmp_path / "nope")}).json()
    assert missing["ok"] is False and "不存在" in missing["error"]

    relative = client.post("/api/reveal",
                           json={"path": "relative/game"}).json()
    assert relative["ok"] is False and "绝对路径" in relative["error"]

    for network in (r"\\server\share\game", "//server/share/game"):
        data = client.post("/api/reveal", json={"path": network}).json()
        assert data["ok"] is False
        assert "网络" in data["error"] or "共享" in data["error"]

    empty = client.post("/api/reveal", json={"path": "  "}).json()
    assert empty["ok"] is False

    assert calls == []      # 非法路径一次都不许拉起资源管理器


def test_reveal_failure_reports_generic_error(client, tmp_path, monkeypatch):
    def boom(_target, *, select):
        raise OSError("explorer 挂了")

    monkeypatch.setattr(app_mod, "_open_in_explorer", boom)
    data = client.post("/api/reveal", json={"path": str(tmp_path)}).json()
    assert data["ok"] is False
    assert "资源管理器" in data["error"]
    # 异常细节只进服务端日志，不回传前端
    assert "explorer 挂了" not in data["error"]


def test_local_path_normalizes_and_rejects_unsafe_forms(tmp_path):
    assert app_mod._local_path("  ", must_exist=False)[0] is None
    assert app_mod._local_path("relative/game", must_exist=False)[0] is None
    assert app_mod._local_path(str(tmp_path / "a" / ".." / "b"),
                               must_exist=False)[0] == tmp_path / "b"
    assert app_mod._local_path(str(tmp_path) + "\x00evil",
                               must_exist=False)[0] is None
    assert app_mod._local_path(r"\\server\share\game", must_exist=False,
                               allow_network=False)[1]
    assert app_mod._local_path(str(tmp_path / "nope"), must_exist=True)[1]


def test_analyze_rejects_unsafe_path_before_job(client):
    for raw in ("relative/game", "C:\\game\x00evil"):
        data = client.post("/api/analyze", json={"path": raw}).json()
        assert data["ok"] is False
        assert "路径" in data["error"]
    assert app_mod.JOBS == {}, "非法路径不得创建任务"


# ---------------------------------------------------------------------------
# 平台校验（GUI 与 CLI 单一口径）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("endpoint", ["/api/full", "/api/package"])
@pytest.mark.parametrize("platforms, needle", [
    ([], "至少选择一个"),
    (["switch"], "未知的打包平台"),
])
def test_platform_validation_returns_400(client, tmp_path, endpoint,
                                         platforms, needle):
    project = tmp_path / "MyGame"
    (project / "game").mkdir(parents=True)
    r = client.post(endpoint, json={"path": str(project),
                                    "platforms": platforms})
    assert r.status_code == 400
    data = r.json()
    assert data["ok"] is False
    assert needle in data["error"]
    if platforms:
        assert "可选" in data["error"]
    assert app_mod.JOBS == {}, "校验失败不得创建任务"
    assert not (tmp_path / "_rtools_work").exists()


# ---------------------------------------------------------------------------
# APK 预分析
# ---------------------------------------------------------------------------

def test_analyze_apk_returns_stats_and_never_unpacks(client, tmp_path,
                                                     monkeypatch):
    apk_path = _make_apk(tmp_path)
    digest = hashlib.sha256(apk_path.read_bytes()).hexdigest()

    def fail_extract(*_a, **_kw):
        pytest.fail("APK 应走只读预分析，不能按压缩包解压")

    monkeypatch.setattr(app_mod.archives, "extract_archive", fail_extract)

    r = client.post("/api/analyze",
                    json={"path": str(apk_path), "mode": "project"})
    assert r.json()["ok"] is True
    job_id = r.json()["job"]
    data = _wait_job(client, job_id)

    result = data["result"]
    assert result["mode"] == "apk"
    assert result["report"]["mode"] == "apk"
    stats = result["apk"]
    for key in ("apk_size", "total_entries", "optimizable", "size",
                "script_count", "signed", "est_saving", "est_saving_remap",
                "remap_gain", "has_renpy_layout"):
        assert key in stats
    assert stats["optimizable"] == 1
    assert stats["has_renpy_layout"] is True

    # 引擎的结构化进度事件（code/params）经由 _job_progress 下发到前端
    codes = [entry.get("code") for entry in data["logs"]]
    assert "apk_analyze_start" in codes
    assert "apk_analyze_done" in codes

    # 只读承诺：原包字节不变；没有产物目录
    assert hashlib.sha256(apk_path.read_bytes()).hexdigest() == digest
    assert not (tmp_path / "_rtools_work").exists()
    assert not (tmp_path / "_rtools_output").exists()

    # 历史里能查到：标题是文件名，摘要只有小字段
    record = next(job for job in jobstore.list_jobs() if job["id"] == job_id)
    assert record["kind"] == "analyze"
    assert record["title"] == "demo.apk"
    assert record["status"] == "done"
    assert record["summary"]["mode"] == "apk"
    assert record["summary"]["optimizable"] == 1
    assert "report" not in record["summary"]


# ---------------------------------------------------------------------------
# 结构化日志与心跳
# ---------------------------------------------------------------------------

def test_job_log_carries_code_and_params(client):
    job_id = app_mod._new_job("analyze", title="结构化日志")
    progress = app_mod._job_progress(job_id)
    progress.emit("scan", "扫描资源 1/2：a", code="scan_progress",
                  params={"i": 1, "total": 2})

    logs = client.get(f"/api/job/{job_id}").json()["logs"]
    entry = logs[-1]
    assert entry["stage"] == "scan"
    assert entry["message"] == "扫描资源 1/2：a"
    assert entry["code"] == "scan_progress"
    assert entry["params"] == {"i": 1, "total": 2}

    # 旧式两参回调继续工作，不会因宽回调改造而报错
    legacy = []
    app_mod.Progress(lambda stage, message:
                     legacy.append((stage, message))).emit(
        "scan", "旧回调", code="scan_progress", params={"i": 1})
    assert legacy == [("scan", "旧回调")]


def test_heartbeat_refreshes_only_active_jobs(monkeypatch):
    calls = []
    monkeypatch.setattr(app_mod.jobstore, "heartbeat",
                        lambda job_id, force=False:
                        calls.append(job_id) or True)
    running = app_mod._new_job("optimize", title="跑着")
    with app_mod.JOBS_LOCK:
        app_mod.JOBS[running]["status"] = "queued"
    finished = app_mod._new_job("analyze", title="完了")
    with app_mod.JOBS_LOCK:
        app_mod.JOBS[finished]["status"] = "done"

    flushed = app_mod._heartbeat_once()
    assert flushed == [running]
    assert calls == [running]


def test_heartbeat_failure_is_swallowed(monkeypatch):
    def boom(*_a, **_kw):
        raise OSError("磁盘不见了")

    monkeypatch.setattr(app_mod.jobstore, "heartbeat", boom)
    job_id = app_mod._new_job("optimize")
    assert app_mod._heartbeat_once() == [job_id]     # 不向任务传播异常

    thread = app_mod._ensure_heartbeat_thread()
    assert thread is app_mod._ensure_heartbeat_thread()   # 单例
    assert thread.daemon is True


def test_queued_cancel_writes_history(client):
    gate = threading.Event()
    running = app_mod._new_job("optimize", title="先跑")
    app_mod._dispatch_job(running, lambda: gate.wait(5))
    queued = app_mod._new_job("slimapk", title="排队")
    app_mod._dispatch_job(queued, lambda: None)
    try:
        assert app_mod.JOBS[queued]["status"] == "queued"
        r = client.post(f"/api/job/{queued}/cancel")
        assert r.json()["ok"] is True
        assert app_mod.JOBS[queued]["status"] == "canceled"

        record = next(job for job in jobstore.list_jobs()
                      if job["id"] == queued)
        assert record["status"] == "canceled"
        assert "取消" in record["error"]
    finally:
        gate.set()
        assert _wait_job(client, running)["status"] == "done"
