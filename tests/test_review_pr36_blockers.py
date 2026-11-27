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

# ---------------------------------------------------------------------------
# 阻断 3：交付目录名幂等 + 用户预置的同名目录绝不被删
# ---------------------------------------------------------------------------

def _make_dist_zip(zip_path: Path, wrapper: str | None = "MyGame") -> Path:
    """造一个最小成品压缩包；wrapper=None 时是"没有顶层文件夹"的包。"""
    prefix = f"{wrapper}/" if wrapper else ""
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(f"{prefix}game/script.rpy", 'label start:\n    "hi"\n')
        zf.writestr(f"{prefix}game/options.rpy", "define x = 1\n")
    return zip_path


def _delivered_tops(run_result: dict) -> list[str]:
    """读回本次交付包的顶层目录名。"""
    with zipfile.ZipFile(run_result["archive_output"]) as zf:
        return sorted({name.split("/")[0] for name in zf.namelist()})


def test_run_dist_smart_is_idempotent_on_same_input(tmp_path):
    """同一个压缩包连跑两次，交付包顶层目录名必须一致。

    回归：撞名修复把 target.exists() 一律让位，第二次变成 MyGame-2，
    玩家解出来的文件夹名开始漂移（启动脚本/快捷方式按目录名找）。
    """
    from rtools.config import OptimizeOptions
    from rtools.pipeline import run_dist_smart

    src = _make_dist_zip(tmp_path / "Game.zip")
    work = tmp_path / "work"
    out = tmp_path / "out"
    names = []
    for _ in range(2):
        r = run_dist_smart(str(src), OptimizeOptions(), str(work), str(out))
        names.append(_delivered_tops(r))
    assert names[0] == ["MyGame"], f"首次交付名不对：{names[0]}"
    assert names[1] == names[0], f"同名重跑后交付目录名漂移了：{names}"


def test_run_dist_smart_is_idempotent_without_wrapper(tmp_path):
    """没有顶层 wrapper 的包同样幂等（旧版会变成 game-解压-2）。"""
    from rtools.config import OptimizeOptions
    from rtools.pipeline import run_dist_smart

    src = _make_dist_zip(tmp_path / "game.zip", wrapper=None)
    work = tmp_path / "work"
    out = tmp_path / "out"
    names = []
    for _ in range(3):
        r = run_dist_smart(str(src), OptimizeOptions(), str(work), str(out))
        names.append(_delivered_tops(r))
    assert names[0] == ["game-解压"]
    assert names[1] == names[0] and names[2] == names[0], \
        f"无 wrapper 的包重跑后交付目录名漂移了：{names}"


def test_run_dist_smart_never_deletes_user_directory(tmp_path):
    """用户预置的同名目录绝不被删，新交付改成让位名。"""
    from rtools.config import OptimizeOptions
    from rtools.pipeline import run_dist_smart

    src = _make_dist_zip(tmp_path / "Game.zip")
    work = tmp_path / "work"
    work.mkdir()
    user_dir = work / "MyGame"
    user_dir.mkdir()
    keep = user_dir / "我的东西.txt"
    keep.write_text("用户自己的文件", encoding="utf-8")

    r = run_dist_smart(str(src), OptimizeOptions(), str(work),
                       str(tmp_path / "out"))
    assert keep.exists() and keep.read_text(encoding="utf-8") == "用户自己的文件", \
        "用户预置的同名目录被删了"
    assert _delivered_tops(r) == ["MyGame-2"], "撞到用户目录时必须让位改名"


def test_claim_delivery_dir_reuses_own_ledger_entry(tmp_path):
    """带归属标记的旧交付目录可以清掉复用；用户目录只让位。"""
    from rtools.pipeline import (_load_owned_deliveries, _claim_delivery_dir,
                                 _mark_delivery)

    work = tmp_path / "work"
    work.mkdir()
    owned = work / "MyGame"
    owned.mkdir()
    (owned / "old.txt").write_text("上次的残留", encoding="utf-8")
    _mark_delivery(work, owned)
    assert "MyGame" in _load_owned_deliveries(work)

    chosen = _claim_delivery_dir(work / "MyGame",
                                 _load_owned_deliveries(work), [])
    assert chosen == work / "MyGame", "自己产出的目录应当原地复用"
    assert not chosen.exists(), "复用前必须先把上次的内容清掉"

    user = work / "Other"
    user.mkdir()
    (user / "sos.txt").write_text("别动我", encoding="utf-8")
    other = _claim_delivery_dir(work / "Other",
                                _load_owned_deliveries(work), [])
    assert other == work / "Other-2", "用户目录必须让位"
    assert (user / "sos.txt").exists(), "用户目录内容绝不能被删"


def test_claim_delivery_dir_ignores_stale_ledger_name(tmp_path):
    """登记表过时：名字记着但目录里没有归属标记，仍算用户的东西。"""
    from rtools.pipeline import _load_owned_deliveries, _claim_delivery_dir

    work = tmp_path / "work"
    work.mkdir()
    stale = work / "MyGame"
    stale.mkdir()
    keep = stale / "用户后来放的.txt"
    keep.write_text("别动", encoding="utf-8")
    # 手写一份"名字记着、但目录里没有标记"的过时台账
    (work / ".rtools-deliveries.json").write_text(
        '{"version": 1, "delivered": ["MyGame"]}', encoding="utf-8")

    chosen = _claim_delivery_dir(work / "MyGame",
                                 _load_owned_deliveries(work), [])
    assert chosen == work / "MyGame-2", "只凭过时台账的名字不得删用户目录"
    assert keep.exists(), "用户目录内容绝不能被删"


def test_delivery_marker_not_inside_archive(tmp_path):
    """归属标记是回包之后才写的，绝不能出现在交付包里。"""
    from rtools.config import OptimizeOptions
    from rtools.pipeline import run_dist_smart

    src = _make_dist_zip(tmp_path / "Game.zip")
    work = tmp_path / "work"
    r = run_dist_smart(str(src), OptimizeOptions(), str(work),
                       str(tmp_path / "out"))
    with zipfile.ZipFile(r["archive_output"]) as zf:
        names = zf.namelist()
    assert not any(".rtools-delivered" in n for n in names), \
        f"归属标记被写进交付包了：{names}"
    # 磁盘上的交付目录里则必须有标记（下次重跑靠它认自己人）
    assert (Path(r["working_dir"]) / ".rtools-delivered").is_file()
