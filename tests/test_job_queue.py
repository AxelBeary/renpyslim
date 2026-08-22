"""任务队列回归测试（用户要求 2026-08-17）。

重任务（瘦身/打包/APK/字体）同一时刻只跑一个，后提交的自动排队，
前一个结束自动接着跑；排队中的任务可直接退队；只读分析不排队。
"""
import threading
import time

import pytest

import web.app as app_mod
from web.app import JOBS, JOBS_LOCK, _dispatch_job, _new_job

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None


def _wait(predicate, timeout=5.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return job["status"] if job else None


def _reset_queue_state():
    """用例间清空全局队列状态，避免互相污染。"""
    with app_mod._QUEUE_LOCK:
        app_mod._QUEUE.clear()
        app_mod._JOB_TASKS.clear()
        app_mod._RUNNING["id"] = None
    with JOBS_LOCK:
        JOBS.clear()


@pytest.fixture(autouse=True)
def clean_state():
    _reset_queue_state()
    yield
    _reset_queue_state()


def test_first_job_runs_immediately():
    gate = threading.Event()
    j1 = _new_job("optimize")
    _dispatch_job(j1, lambda: gate.wait(5))
    assert _wait(lambda: _status(j1) == "running")   # 立即开跑，不进队列
    gate.set()
    assert _wait(lambda: _status(j1) == "done")


def test_second_heavy_job_queues_then_runs():
    gate = threading.Event()
    j1 = _new_job("optimize")
    _dispatch_job(j1, lambda: gate.wait(5))
    j2 = _new_job("slimapk")
    _dispatch_job(j2, lambda: None)
    assert _status(j2) == "queued"          # 有任务在跑：排队等位
    gate.set()                               # 前一个结束：自动接着跑
    assert _wait(lambda: _status(j1) == "done")
    assert _wait(lambda: _status(j2) == "done")


def test_cancel_queued_job_skipped():
    gate = threading.Event()
    j1 = _new_job("optimize")
    _dispatch_job(j1, lambda: gate.wait(5))
    j2 = _new_job("optimize")
    _dispatch_job(j2, lambda: (_ for _ in ()).throw(
        AssertionError("排队的任务被取消后不应执行")))
    with JOBS_LOCK:
        JOBS[j2]["status"] = "canceled"      # 同 /api/job/{id}/cancel 的排队分支
    gate.set()
    assert _wait(lambda: _status(j1) == "done")
    time.sleep(0.3)                          # 给调度器跳过 j2 的时间
    assert _status(j2) == "canceled"
    assert app_mod._RUNNING["id"] is None    # 队列清空后收工


def test_error_finish_still_advances_queue():
    j1 = _new_job("optimize")
    _dispatch_job(j1, lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    j2 = _new_job("full")
    _dispatch_job(j2, lambda: None)
    assert _wait(lambda: _status(j1) == "error")
    assert _wait(lambda: _status(j2) == "done")   # 前一个崩了也要接着跑


def test_three_jobs_serial_never_more_than_one_running():
    """排队 3 个任务：任意时刻最多 1 个在跑，且按提交顺序串行。"""
    counter = {"running": 0, "max": 0}
    lock = threading.Lock()
    order = []

    def make(name, gate, wait_secs):
        def task():
            with lock:
                counter["running"] += 1
                counter["max"] = max(counter["max"], counter["running"])
            order.append(name)
            gate.wait(wait_secs)
            with lock:
                counter["running"] -= 1
        return task

    gate1 = threading.Event()
    j1 = _new_job("optimize")
    _dispatch_job(j1, make("a", gate1, 5))
    j2 = _new_job("optimize")
    _dispatch_job(j2, make("b", threading.Event(), 0.2))
    j3 = _new_job("optimize")
    _dispatch_job(j3, make("c", threading.Event(), 0.2))
    assert _status(j2) == "queued" and _status(j3) == "queued"
    gate1.set()
    assert _wait(lambda: all(_status(j) == "done" for j in (j1, j2, j3)),
                 timeout=10.0)
    assert counter["max"] <= 1      # 并发计数器：任意时刻运行数 ≤ 1
    assert order == ["a", "b", "c"]  # 链式推进按提交顺序
    assert app_mod._RUNNING["id"] is None


def test_cleanup_exempts_queued_jobs():
    """任务清理（超 2 小时/只留 30）不得删排队中的任务。"""
    # ① 超时清理分支：超过 2 小时但还在排队的任务不被删
    j_old_queued = _new_job("optimize")
    with JOBS_LOCK:
        JOBS[j_old_queued]["status"] = "queued"
        JOBS[j_old_queued]["created"] -= 7300
    _new_job("analyze")   # 触发一轮清理
    assert j_old_queued in JOBS
    assert _status(j_old_queued) == "queued"

    # ② 只留最近 30 分支：最老的排队任务同样豁免，只删已结束的
    with JOBS_LOCK:
        JOBS.clear()
    for i in range(31):
        with JOBS_LOCK:
            JOBS[f"done{i}"] = {
                "id": f"done{i}", "kind": "optimize", "status": "done",
                "logs": [], "result": None, "error": None,
                "cancel": False, "created": 1000.0 + i,
            }
    with JOBS_LOCK:
        JOBS["keep_queued"] = {
            "id": "keep_queued", "kind": "optimize", "status": "queued",
            "logs": [], "result": None, "error": None,
            "cancel": False, "created": 999.0,   # 全场最老，但排队中
        }
    _new_job("analyze")
    assert "keep_queued" in JOBS


@pytest.mark.skipif(TestClient is None, reason="缺少测试客户端依赖")
def test_optimize_invalid_mode_400(tmp_path):
    with TestClient(app_mod.app,
                    base_url="http://127.0.0.1:52786") as client:
        r = client.post("/api/optimize",
                        json={"path": str(tmp_path), "mode": "bogus"})
        assert r.status_code == 400


@pytest.mark.skipif(TestClient is None, reason="缺少测试客户端依赖")
def test_api_jobs_and_queued_cancel():
    gate = threading.Event()
    j1 = _new_job("optimize")
    _dispatch_job(j1, lambda: gate.wait(5))
    j2 = _new_job("slimapk")
    _dispatch_job(j2, lambda: None)
    try:
        with TestClient(app_mod.app,
                        base_url="http://127.0.0.1:52786") as client:
            r = client.get("/api/jobs")
            assert r.json()["ok"] is True
            listed = {j["id"]: j for j in r.json()["jobs"]}
            assert listed[j1]["status"] == "running"
            assert listed[j2]["status"] == "queued"

            # 排队中取消：直接退队，不用等引擎干完手头活
            r = client.post(f"/api/job/{j2}/cancel")
            assert r.json()["ok"] is True
            assert _status(j2) == "canceled"

            # 任务详情带类型字段（前端断线重连判断停止键显隐要用）
            r = client.get(f"/api/job/{j1}")
            assert r.json()["kind"] == "optimize"
    finally:
        gate.set()
        assert _wait(lambda: _status(j1) == "done")
