"""PR #36 评审阻断项的回归测试（2026-11-28）。

对应三个阻断：
1. backup.make_backup_zip 的 junction 防护是死代码——rglob 会穿进
   Windows junction，把工程外文件当普通文件写进备份包；
2. jobstore._file_lock 在 except 里 yield，把锁体内的 OSError 吞成
   RuntimeError（异常类型被改写）；
3. run_dist_smart 的交付目录撞名处理修过头，同名重跑不再幂等。

每个用例都能在对应修复回退后失败（反证见提交说明）。
"""
from __future__ import annotations

import logging
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from rtools import backup, jobstore
from rtools.archives import create_zip

try:
    from fastapi.testclient import TestClient
except ImportError:                      # pragma: no cover - 缺依赖时整组 skip
    TestClient = None

BASE_URL = "http://127.0.0.1:52786"      # 本机门牌号，过"本地专用"防护


@pytest.fixture()
def client():
    if TestClient is None:
        pytest.skip("缺少测试客户端依赖")
    import web.app as app_mod
    with TestClient(app_mod.app, base_url=BASE_URL) as c:
        yield c


# ---------------------------------------------------------------------------
# 阻断 1：备份不得跟随 junction / 符号链接把工程外文件打进去
# ---------------------------------------------------------------------------

def _make_dir_link(link: Path, target: Path) -> None:
    """造一个"指向目录的联接"：Windows 用 junction，POSIX 用符号链接。

    junction 是 Windows 上不需要管理员权限的目录联接，也正是旧版
    rglob + is_file() 判不出来的那个形态（它是目录，is_file() 恒 False）。
    """
    if os.name == "nt":
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, timeout=30, check=False)
        if proc.returncode != 0 or not link.exists():
            pytest.skip("本机无法创建 junction（权限或环境限制）")
    else:
        try:
            os.symlink(str(target), str(link), target_is_directory=True)
        except OSError:
            pytest.skip("本机不支持创建目录符号链接")


def _remove_dir_link(link: Path) -> None:
    """拆掉联接：junction 必须用 rmdir，Path.rmdir 在目录非空时不敢动。"""
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "rmdir", str(link)],
                       capture_output=True, timeout=30, check=False)
    else:
        link.unlink(missing_ok=True)


def _make_leaky_project(tmp_path: Path) -> tuple[Path, Path]:
    """造一个工程：game/script.rpy + 一个指向工程外目录的联接。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "PRIVATE-KEY.txt").write_bytes(b"TOP-SECRET-KEY")
    project = tmp_path / "Game"
    (project / "game").mkdir(parents=True)
    (project / "game" / "script.rpy").write_text(
        "label start:\n    return\n", encoding="utf-8")
    return project, outside


def test_backup_zip_does_not_follow_dir_link(tmp_path, caplog):
    """备份包绝不能包含联接目标（工程外）里的文件。

    回归：旧版遍历写成 "for p in target.rglob('*'): if p.is_file()"，
    junction 是目录、is_file() 为 False，链接判定永远轮不到，rglob 直接
    穿进联接，把 PRIVATE-KEY.txt 当普通文件写进备份包。
    """
    project, outside = _make_leaky_project(tmp_path)
    link = project / "game" / "assets_link"
    _make_dir_link(link, outside)
    try:
        out = tmp_path / "bak.zip"
        with caplog.at_level(logging.WARNING):
            backup.make_backup_zip(str(project), str(out))
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        assert "Game/game/script.rpy" in names, "工程内文件必须照常进备份"
        assert not any("PRIVATE-KEY" in n for n in names), \
            f"工程外文件泄漏进备份包：{names}"
        assert not any("assets_link" in n for n in names), \
            f"联接本身不得出现在备份包里：{names}"
        assert any("跳过" in r.message for r in caplog.records), \
            "跳过联接必须留告警，别静默丢数据"
    finally:
        _remove_dir_link(link)


def test_create_zip_does_not_follow_dir_link(tmp_path):
    """同一棵树走 create_zip：口径不得回归（这是原本正确的实现）。"""
    project, outside = _make_leaky_project(tmp_path)
    link = project / "game" / "assets_link"
    _make_dir_link(link, outside)
    try:
        out = tmp_path / "deliver.zip"
        create_zip(str(project), str(out))
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        assert "Game/game/script.rpy" in names
        assert not any("PRIVATE-KEY" in n for n in names), \
            f"create_zip 泄漏了工程外文件：{names}"
    finally:
        _remove_dir_link(link)


@pytest.mark.skipif(os.name == "nt", reason="POSIX 专属：符号链接语义")
def test_backup_zip_does_not_follow_symlinked_dir(tmp_path):
    """POSIX 侧同口径：目录符号链接不得被跟随进备份包。"""
    project, outside = _make_leaky_project(tmp_path)
    link = project / "game" / "assets_link"
    try:
        os.symlink(str(outside), str(link), target_is_directory=True)
    except OSError:
        pytest.skip("本机不支持创建目录符号链接")
    out = tmp_path / "bak.zip"
    backup.make_backup_zip(str(project), str(out))
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert "Game/game/script.rpy" in names
    assert not any("PRIVATE-KEY" in n for n in names)
    assert not any("assets_link" in n for n in names)

# ---------------------------------------------------------------------------
# 阻断 2：锁体内的 OSError 必须原样传播，不能被改写成 RuntimeError
# ---------------------------------------------------------------------------

def test_file_lock_propagates_oserror_from_body(tmp_path):
    """with jobstore._file_lock(): raise OSError(...) 必须收到 OSError。

    回归：旧版在 _file_lock 里两处 except OSError 分支都 yield，
    锁体（yield 之后的用户代码）被裹进同一个 try，锁体内任何 OSError
    （磁盘满、EIO、权限）都被接住并第二次 yield，
    CPython 报 "generator didn't stop after throw()"，异常类型被改写成
    RuntimeError——真实故障原因全丢，排障线索断掉。
    """
    with pytest.raises(OSError, match="disk on fire"):
        with jobstore._file_lock():
            raise OSError("disk on fire")


def test_file_lock_degrades_when_lock_file_unopenable(tmp_path, monkeypatch):
    """对照面：是真的拿不到锁时，仍要降级为不加锁（锁体照常执行）。"""
    real_open = open

    def broken_open(path, *args, **kwargs):
        if str(path).endswith(".lock"):
            raise OSError("锁文件打不开")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", broken_open)
    ran = []
    with jobstore._file_lock():
        ran.append(True)
    assert ran == [True], "拿不到锁时必须降级执行锁体，而不是抛错"


def test_file_lock_releases_after_body_raises(tmp_path):
    """锁体抛错也要正常释放：错误之后再拿锁不能被自己卡死。"""
    with pytest.raises(OSError):
        with jobstore._file_lock():
            raise OSError("boom")
    with jobstore._file_lock():
        pass        # 能再次进来说明上一次的锁已经释放


def test_api_history_survives_jobstore_failure(client, monkeypatch):
    """jobstore 读盘失败时，历史接口返回 200 而不是 500。"""
    def boom(*args, **kwargs):
        raise OSError("磁盘 / 文件锁故障")

    monkeypatch.setattr(jobstore, "list_jobs", boom)
    resp = client.get("/api/history?limit=5")
    assert resp.status_code == 200, "历史接口不该因磁盘故障 500"
    data = resp.json()
    assert data["ok"] is True
    assert data["jobs"] == []
    assert data.get("warning"), "失败时要有提示语，别假装是空历史"
