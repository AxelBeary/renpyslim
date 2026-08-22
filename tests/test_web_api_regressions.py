"""收口修复回归测试（2026-08-23 三维评审点名缺口，C 路）。

覆盖三处：
① 浏览框超时不放锁的兜底——挂住的假对话框线程超时后锁保持占用、
   线程结束自动释放；持有超过上限被强制重置，且旧线程迟到释放
   不会误伤新一代的锁（代际令牌幂等保护）。
② SlimApkReq 签名参数透传——key_pass / new_key_password 原样到达
   apk.slim_apk。
③ Web 压缩包输入强制 dist——无论传什么 mode 都走 run_dist_smart，
   显式传 project 时有纠正日志。
"""
import logging
import threading
import time
import zipfile

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None

import web.app as app_mod

pytestmark = pytest.mark.skipif(TestClient is None, reason="缺少测试客户端依赖")


@pytest.fixture()
def client():
    # base_url 指定本机门牌号，避免被"本地专用"防护拦下
    with TestClient(app_mod.app, base_url="http://127.0.0.1:52786") as c:
        yield c


@pytest.fixture(autouse=True)
def reset_browse_state():
    """用例前后复位浏览锁状态，避免互相污染。"""
    with app_mod._BROWSE_META_LOCK:
        app_mod._BROWSE_STATE.update({"held": False, "since": None, "token": 0})
    yield
    with app_mod._BROWSE_META_LOCK:
        app_mod._BROWSE_STATE.update({"held": False, "since": None, "token": 0})


def _wait_job_done(client, job_id, timeout=10.0) -> dict:
    """轮询任务直到终态，返回最终响应；非 done 直接断言失败。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/job/{job_id}").json()
        if data["status"] in ("done", "error", "canceled"):
            break
        time.sleep(0.02)
    assert data["status"] == "done", f"任务未正常完成：{data}"
    return data


def test_browse_timeout_keeps_lock_then_auto_releases(client, monkeypatch):
    """对话框线程挂住：接口报超时且锁保持占用；线程结束后锁自动释放。"""
    gate = threading.Event()

    def fake_dialog(kind):
        gate.wait(10)          # 模拟卡死的对话框
        return ""

    monkeypatch.setattr(app_mod, "_browse_open_dialog", fake_dialog)
    monkeypatch.setattr(app_mod, "_BROWSE_JOIN_TIMEOUT", 0.2)   # 注入短超时

    r = client.get("/api/browse")
    data = r.json()
    assert data["ok"] is False
    assert "超时" in data["error"]
    assert "重启工具" in data["error"]        # 文案含兜底提示
    # 线程还没结束：锁保持占用，再来浏览被拒
    assert app_mod._BROWSE_STATE["held"] is True
    r = client.get("/api/browse")
    assert r.json()["ok"] is False

    gate.set()                 # 放线程走完：锁应被自己释放
    deadline = time.time() + 5.0
    while time.time() < deadline and app_mod._BROWSE_STATE["held"]:
        time.sleep(0.02)
    assert app_mod._BROWSE_STATE["held"] is False


def test_browse_force_reset_after_max_hold_and_stale_release(client, monkeypatch,
                                                             caplog):
    """持有超过上限：新请求强制重置并开新对话框；旧代际释放不生效。"""
    gate = threading.Event()
    monkeypatch.setattr(app_mod, "_browse_open_dialog",
                        lambda kind: (gate.wait(10), "/picked")[1])
    monkeypatch.setattr(app_mod, "_BROWSE_JOIN_TIMEOUT", 0.2)

    # 伪造一个已卡 700 秒的旧锁（默认上限 600 秒），代际记为 5
    with app_mod._BROWSE_META_LOCK:
        app_mod._BROWSE_STATE.update(
            {"held": True, "since": time.time() - 700, "token": 5})

    with caplog.at_level(logging.WARNING, logger="renpyslim.web"):
        r = client.get("/api/browse")
    # 强制重置生效：新对话框成功开出（本例它也会因短 join 超时，
    # 关键是"锁被重置、新请求得以持有"而不是被旧锁挡住）
    assert r.json()["ok"] is False and "超时" in r.json()["error"]
    assert app_mod._BROWSE_STATE["held"] is True
    assert app_mod._BROWSE_STATE["token"] == 6      # 代际已前进
    assert any("强制重置" in m for m in caplog.messages)

    # 旧线程（代际 5）迟到苏醒后的释放必须是空操作
    app_mod._browse_release(5)
    assert app_mod._BROWSE_STATE["held"] is True
    assert app_mod._BROWSE_STATE["token"] == 6

    gate.set()                 # 新线程走完按自己的代际正常放锁
    deadline = time.time() + 5.0
    while time.time() < deadline and app_mod._BROWSE_STATE["held"]:
        time.sleep(0.02)
    assert app_mod._BROWSE_STATE["held"] is False


def test_browse_held_within_limit_not_reset(client, monkeypatch):
    """持有未超上限：不许强制重置，仍然拒开第二个对话框。"""
    gate = threading.Event()
    monkeypatch.setattr(app_mod, "_browse_open_dialog",
                        lambda kind: (gate.wait(10), "/x")[1])
    monkeypatch.setattr(app_mod, "_BROWSE_JOIN_TIMEOUT", 0.2)
    try:
        r = client.get("/api/browse")          # 开第一个（join 超时，锁保持）
        assert r.json()["ok"] is False
        r = client.get("/api/browse")          # 未超 600 秒：拒绝
        assert r.json()["ok"] is False
        assert "已有一个选择框打开" in r.json()["error"]
    finally:
        gate.set()


def test_slimapk_passes_key_passwords(client, monkeypatch, tmp_path):
    """SlimApkReq 的签名参数必须同名透传到 apk.slim_apk。"""
    fake_apk = tmp_path / "fake.apk"
    fake_apk.write_bytes(b"PK\x03\x04fake")
    captured = {}

    def fake_slim(path, preset, **kw):
        captured["path"] = path
        captured["preset"] = preset
        captured.update(kw)
        return {"output": str(fake_apk), "saved_bytes": 0, "warnings": []}

    monkeypatch.setattr(app_mod.apk, "slim_apk", fake_slim)
    r = client.post("/api/slimapk", json={
        "path": str(fake_apk), "preset": "balanced",
        "keystore": "E:/keys/my.ks", "ks_pass": "kspass1",
        "key_alias": "myalias", "key_pass": "KEYPASS-123",
        "new_key_password": "NEWPASS-456"})
    assert r.json()["ok"] is True
    _wait_job_done(client, r.json()["job"])

    assert captured["key_pass"] == "KEYPASS-123"
    assert captured["new_key_password"] == "NEWPASS-456"
    assert captured["keystore"] == "E:/keys/my.ks"
    assert captured["ks_pass"] == "kspass1"
    assert captured["key_alias"] == "myalias"
    assert captured["preset"] == "balanced"


def test_archive_input_forces_dist_with_correction_log(client, monkeypatch,
                                                       tmp_path, caplog):
    """压缩包输入无论传什么 mode 都走 dist 通道；传 project 时有纠正日志。"""
    arch = tmp_path / "game.zip"
    with zipfile.ZipFile(str(arch), "w") as zf:
        zf.writestr("MyGame/game/cache/shader.txt", b"x")
        zf.writestr("MyGame/MyGame.exe", b"EXE")

    calls = []

    def fake_run_dist_smart(path, options, work_root, output_dir,
                            progress=None, password=None, cancel=None):
        calls.append({"path": path, "password": password})
        return {"saved_bytes": 0}

    monkeypatch.setattr(app_mod.pipeline, "run_dist_smart", fake_run_dist_smart)

    # ① 显式传 project：必须纠正到 dist 并留下警告日志
    with caplog.at_level(logging.WARNING, logger="renpyslim.web"):
        r = client.post("/api/optimize",
                        json={"path": str(arch), "mode": "project"})
    assert r.json()["ok"] is True
    _wait_job_done(client, r.json()["job"])
    assert len(calls) == 1                     # 确实走了 dist 通道
    assert calls[0]["path"] == str(arch)
    assert any("已自动改走 dist" in m for m in caplog.messages)

    # ② 传 dist 与不传 mode：同样进 dist 通道，无需求纠正
    for mode in ("dist", None):
        body = {"path": str(arch)}
        if mode is not None:
            body["mode"] = mode
        r = client.post("/api/optimize", json=body)
        assert r.json()["ok"] is True
        _wait_job_done(client, r.json()["job"])
    assert len(calls) == 3
