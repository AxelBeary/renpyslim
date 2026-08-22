"""后端"本地专用"防护回归测试：只认本机来源，防恶意网页指挥本地服务。"""
import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None

from web.app import app

pytestmark = pytest.mark.skipif(TestClient is None, reason="缺少测试客户端依赖")


@pytest.fixture(scope="module")
def client():
    # base_url 决定 Host 门牌号；模拟真实的本机浏览器访问
    with TestClient(app, base_url="http://127.0.0.1:52786") as c:
        yield c


def test_normal_local_request_allowed(client):
    r = client.get("/api/env")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_local_origin_allowed(client):
    r = client.get("/api/env", headers={"origin": "http://127.0.0.1:52786"})
    assert r.status_code == 200


def test_foreign_host_rejected(client):
    """DNS 重绑定手法：Host 门牌号不是本机地址。"""
    r = client.get("/api/env", headers={"host": "evil.example.com"})
    assert r.status_code == 403


def test_foreign_origin_rejected(client):
    """跨站来源：Origin 来自别的网站。"""
    r = client.post("/api/shutdown",
                    headers={"origin": "https://evil.example.com"})
    assert r.status_code == 403


def test_null_origin_rejected(client):
    """沙盒化 iframe 等场景会发 Origin: null，解析不出主机，必须拒绝。"""
    r = client.get("/api/env", headers={"origin": "null"})
    assert r.status_code == 403


def test_empty_origin_rejected(client):
    """Origin 头存在但为空串：同样解析不出主机，必须拒绝。"""
    r = client.get("/api/env", headers={"origin": ""})
    assert r.status_code == 403


def test_garbage_origin_rejected(client):
    """非法值（解析不出主机名）：拒绝。"""
    r = client.get("/api/env", headers={"origin": "not a url %%%"})
    assert r.status_code == 403
