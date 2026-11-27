"""issue #39 回归：取消后的落盘、记账与外部进程。

三件事必须同时成立，缺一条用户都会看到"产物变了、报告却说没变"：

1. 取消后不再替换/删除文件——优化器在落盘点做协作式取消检查，
   要么不留痕，要么留痕且记账；
2. 取消后不再新起外部进程——任务 scope 有持久取消标记，run_quiet
   在启动前与登记后都检查，不靠 kill_children 的一次性快照；
3. partial_results 完整——取消前已经跑完的成果一条不落，
   changelog 里记的和磁盘上发生的一致。

时序全部由 Event / 取消标记驱动：测试等的是"取消已经被观察到"，
不是 sleep 里抢窗口。
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

from rtools import procutil
from rtools.image_optimizer import optimize_image
from rtools.models import ChangeRecord, Progress
from rtools.pipeline import (
    PipelineCancelled,
    _run_jobs,
    _run_jobs_or_flush,
)


# ---------------------------------------------------------------------------
# 夹具与工具
# ---------------------------------------------------------------------------

def _make_png(path: Path, size: int = 96, seed: int = 0) -> None:
    """造一张必然还能再压小的 PNG。

    纯噪点压不动（Pillow 的 optimize 已到极限），会让用例走 skipped
    分支而测不到落盘点；纯色底图 + 少量噪点才能稳定命中"改写目标"。
    以 compress_level=0 存盘，优化器再压一定变小。
    """
    import random

    from PIL import Image

    rnd = random.Random(seed)
    im = Image.new("RGB", (size, size), (30, 60, 90))
    pixels = im.load()
    for i in range(0, size * size, 37):
        pixels[i % size, (i * 7) % size] = (
            rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "PNG", compress_level=0)


def _result(saved: int = 0, records: list | None = None) -> dict:
    """优化任务的标准返回（记账以 failed/skipped 字段为准）。"""
    return {"records": records or [], "saved": saved, "rename": None,
            "remap": None, "rpa": None, "warn": None,
            "failed": None, "skipped": None}


class _ScopedRun:
    """在自有任务 scope 里跑一段流水线，并把取消标记暴露给测试。

    测试要等的是"取消确实已经落到这个令牌上"（kill_children 先标记、
    再杀进程），而不是"我喊了取消"——否则会退化成靠 sleep 猜时序。
    """

    def __init__(self, target):
        self.target = target
        self.scope = None
        self.outcome: dict = {}
        self._ready = threading.Event()
        self.thread = threading.Thread(target=self._run)

    def _run(self):
        with procutil.task_scope() as scope:
            self.scope = scope
            self._ready.set()
            try:
                self.target()
            except PipelineCancelled as exc:
                self.outcome["partial"] = exc.partial_results
            except BaseException as exc:        # noqa: BLE001 - 供断言展示
                self.outcome["error"] = exc

    def __enter__(self):
        self.thread.start()
        assert self._ready.wait(30), "流水线没有进入任务 scope"
        return self

    def wait_cancelled(self, timeout: float = 30.0) -> bool:
        """等这个令牌真的被标成已取消（kill_children 已跑过）。"""
        deadline = threading.Event()
        end = __import__("time").monotonic() + timeout
        while __import__("time").monotonic() < end:
            if procutil.is_cancelled(self.scope):
                return True
            deadline.wait(0.01)
        return False

    def __exit__(self, *exc_info):
        self.thread.join(timeout=120)
        assert not self.thread.is_alive(), "取消收尾没有返回"
        return False


def _wait_until(predicate, timeout: float = 30.0) -> None:
    """等一个条件成立（不靠 sleep 猜多久够）。"""
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        threading.Event().wait(0.01)
    raise AssertionError("等待条件超时")


# ---------------------------------------------------------------------------
# 1. 取消后晚完成的纯 Python 优化不得留脏产物
# ---------------------------------------------------------------------------

def test_cancelled_optimizer_does_not_replace_file(tmp_path):
    """取消后放行纯 Python 优化器：不得替换目标文件（issue #39 主诉）。"""
    src = tmp_path / "pic.png"
    dst = tmp_path / "out" / "pic.png"
    _make_png(src)
    dst.parent.mkdir(parents=True)      # 落盘点确实可达，才测得到取消检查
    entered, release, cancel = (threading.Event(), threading.Event(),
                                threading.Event())

    def job():
        entered.set()
        assert release.wait(30), "测试自己没放行，属用例缺陷"
        res = optimize_image(str(src), str(dst), 80)
        return _result(max(0, res.get("old_size", 0) - res.get("new_size", 0)))

    with _ScopedRun(lambda: _run_jobs(Progress(), "test", [("image", job)],
                                      cancel.is_set)) as run:
        assert entered.wait(30), "任务没有进入优化器"
        cancel.set()
        assert run.wait_cancelled(), "取消没有落到任务令牌上"
        release.set()                       # 取消之后优化器才走到落盘点

    assert "error" not in run.outcome, run.outcome.get("error")
    assert not dst.exists(), "取消后仍留下了产物（产物有、changelog 无）"
    assert run.outcome["partial"] == [], "没落盘却报了成果，两边对不上"
    leftovers = list(dst.parent.glob("*")) if dst.parent.exists() else []
    assert leftovers == [], f"取消后留下临时残骸：{leftovers}"


def test_optimizer_without_cancel_still_writes(tmp_path):
    """反向守护：没有取消时优化器照常落盘（别把正常路径一起关掉）。"""
    src = tmp_path / "pic.png"
    dst = tmp_path / "out" / "pic.png"
    _make_png(src, seed=7)
    dst.parent.mkdir(parents=True)
    with procutil.task_scope():
        res = optimize_image(str(src), str(dst), 80)
    assert res["status"] == "ok", res.get("reason")
    assert dst.exists()


# ---------------------------------------------------------------------------
# 2. 取消后不再新起外部进程
# ---------------------------------------------------------------------------

def test_run_quiet_refuses_after_scope_cancelled(monkeypatch):
    """scope 一旦取消，run_quiet 在 Popen 之前就拒绝启动。"""
    started: list = []

    def fake_popen(cmd, **kwargs):     # pragma: no cover - 走到这里即失败
        started.append(cmd)
        raise AssertionError("已取消仍尝试启动外部进程")

    monkeypatch.setattr(procutil.subprocess, "Popen", fake_popen)
    with procutil.task_scope() as scope:
        procutil.kill_children(scope=scope)        # 走真实取消路径
        assert procutil.is_cancelled(scope)
        with pytest.raises(procutil.TaskCancelled):
            procutil.run_quiet([sys.executable, "-c", "pass"])
    assert started == []


def test_worker_starting_late_does_not_spawn_subprocess():
    """取消瞬间还没起子进程的 worker：之后启动必须被拦下（issue #39）。"""
    entered = threading.Event()
    release = threading.Event()
    spawned: list = []

    def late_job():
        entered.set()
        assert release.wait(30)
        # 这时取消收尾（kill_children + shutdown）早已跑完
        spawned.append(procutil.run_quiet(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            capture_output=True))

    with _ScopedRun(lambda: _run_jobs(Progress(), "test",
                                      [("late", late_job)], lambda: True)) as run:
        assert entered.wait(30), "任务没有起来"
        assert run.wait_cancelled(), "取消没有落到任务令牌上"
        release.set()                   # 取消之后才轮到它起外部进程

    assert "error" not in run.outcome, run.outcome.get("error")
    assert spawned == [], "取消后仍启动了外部进程"
    assert run.outcome["partial"] == []


def test_cancel_marks_only_own_scope():
    """取消只影响自己的令牌：并发任务不受牵连（既有隔离语义）。"""
    with procutil.task_scope() as mine:
        with procutil.task_scope() as other:
            procutil.kill_children(scope=mine)
            assert procutil.is_cancelled(mine)
            assert not procutil.is_cancelled(other)
            with pytest.raises(procutil.TaskCancelled):
                procutil.check_cancelled(mine)      # 只查自己的令牌
            assert not procutil.is_cancelled(other)
    # 令牌退出时标记一并清掉，不会漏到下一轮
    assert not procutil.is_cancelled(mine)


def test_uncancelled_scope_is_unaffected():
    """没取消的普通 scope 与无令牌场景不受影响（别把正常任务拦了）。"""
    with procutil.task_scope() as scope:
        assert not procutil.is_cancelled(scope)
        procutil.check_cancelled()              # 不抛即正确
    assert not procutil.is_cancelled()
    procutil.check_cancelled()


# ---------------------------------------------------------------------------
# 3. partial_results 完整 + changelog 与磁盘同口径
# ---------------------------------------------------------------------------

def test_partial_results_include_all_finished_jobs(tmp_path):
    """取消时已跑完的任务全部进 partial_results，一条不落。"""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    finished: list = []
    slow_entered = threading.Event()
    release = threading.Event()
    cancel = threading.Event()

    def quick(i):
        def fn():
            finished.append(i)
            return _result(10 * (i + 1))
        return (f"q{i}", fn)

    def slow():
        slow_entered.set()
        assert release.wait(30)
        return _result(0)

    jobs = [quick(i) for i in range(4)] + [("slow", slow)]
    target = lambda: _run_jobs_or_flush(                        # noqa: E731
        Progress(), "optimize", jobs, cancel.is_set, str(out_dir), [], 0)

    with _ScopedRun(target) as run:
        assert slow_entered.wait(30), "慢任务没有起来"
        _wait_until(lambda: len(finished) == 4)     # 等快任务全部落地
        cancel.set()
        assert run.wait_cancelled(), "取消没有落到任务令牌上"
        release.set()

    assert "error" not in run.outcome, run.outcome.get("error")
    assert finished == [0, 1, 2, 3]
    partial = run.outcome["partial"]
    saved = sum(r.get("saved", 0) for r in partial)
    assert saved == sum(10 * (i + 1) for i in range(4)), \
        f"取消前的成果丢账：{partial}"
    data = json.loads((out_dir / "changelog.json").read_text(encoding="utf-8"))
    assert data["cancelled"] is True
    assert data["saved_bytes"] == saved
    assert len(data["records"]) == 0            # 这几个任务没有具体记录


def test_cancel_changelog_matches_disk_state(tmp_path):
    """真实优化器 + 假任务混合：落盘过的一定有账，没落盘的一定没有账。

    这是 issue #39 的核心不变量——取消之后要么不留痕（产物没有、
    清单也没有），要么留痕且记账（产物有、清单也有），两者只能居其一。
    """
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    src = tmp_path / "pic.png"
    dst = tmp_path / "game" / "pic.png"
    _make_png(src, seed=3)
    dst.parent.mkdir(parents=True)      # 落盘点可达，取消检查才有意义
    entered, release, cancel = threading.Event(), threading.Event(), threading.Event()

    def quick(i):
        def fn():
            return _result(10, [ChangeRecord(action="compress",
                                             src=f"{i}.png", detail="x")])
        return (f"j{i}", fn)

    def late_image():
        entered.set()
        assert release.wait(30)
        res = optimize_image(str(src), str(dst), 80)
        # 只有真的落盘了才记这一笔——记账口径跟着 status 走
        if res["status"] != "ok":
            return _result(0)
        return _result(res["old_size"] - res["new_size"],
                       [ChangeRecord(action="compress", src="game/pic.png",
                                     detail="晚完成")])

    jobs = [quick(i) for i in range(3)] + [("image", late_image)]
    target = lambda: _run_jobs_or_flush(                        # noqa: E731
        Progress(), "optimize", jobs, cancel.is_set, str(out_dir), [], 0)

    with _ScopedRun(target) as run:
        assert entered.wait(30)
        cancel.set()
        assert run.wait_cancelled(), "取消没有落到任务令牌上"
        release.set()                   # 取消之后才轮到真实优化器落盘

    assert "error" not in run.outcome, run.outcome.get("error")
    partial = run.outcome["partial"]
    data = json.loads((out_dir / "changelog.json").read_text(encoding="utf-8"))
    assert data["cancelled"] is True

    recorded = {rec["src"] for rec in data["records"]}
    # 不变量：产物在 ⇔ 台账在
    assert dst.exists() == ("game/pic.png" in recorded), (
        f"产物与台账对不上：dst.exists()={dst.exists()}，"
        f"清单记录={sorted(recorded)}")
    # 早先完成的三笔照常记账，不被取消吞掉
    assert {"0.png", "1.png", "2.png"} <= recorded
    assert len(data["records"]) == len(partial)
    assert data["saved_bytes"] == sum(r.get("saved", 0) for r in partial)

# ---------------------------------------------------------------------------
# 4. 端到端：真实 run_dist 取消后，产物与 changelog 仍然同口径
# ---------------------------------------------------------------------------

def test_run_dist_cancel_keeps_product_and_ledger_in_sync(tmp_path):
    """真实成品线取消：既不重复记账，也不漏账（issue #39 端到端）。"""
    from rtools import pipeline
    from rtools.config import OptimizeOptions

    game = tmp_path / "MyGame" / "game"
    game.mkdir(parents=True)
    (game / "script.rpy").write_text("label start:\n    'hi'\n",
                                     encoding="utf-8")
    for i in range(4):
        _make_png(game / f"pic{i}.png", seed=i)
    out_dir = tmp_path / "out"
    opts = OptimizeOptions()
    opts.do_audio = False
    opts.do_fonts = False
    # 关掉增量缓存：否则第二次跑同一批图会缓存命中，
    # 走不到 optimize_image，用例就白测了（实测踩过）。
    opts.use_cache = False

    # 取消开关：数到第一张图处理完就喊停，让这一批中途被取消。
    # 计数打在 pipeline 命名空间里的 optimize_image 上——成品线用的
    # 就是这个名字，打在 image_optimizer 模块上不生效。
    written: list = []          # 真的被替换掉的产物（相对 game/ 的路径）
    runs = {"n": 0}
    real_optimize = pipeline.optimize_image

    def counting_optimize(src, dst, *args, **kwargs):
        res = real_optimize(src, dst, *args, **kwargs)
        runs["n"] += 1
        if res["status"] == "ok":
            written.append(Path(src).name)
        return res

    pipeline.optimize_image = counting_optimize
    caught: dict = {}

    def run():
        try:
            pipeline.run_dist(str(tmp_path / "MyGame"), opts,
                              str(tmp_path / "work"), str(out_dir),
                              Progress(), cancel=lambda: runs["n"] >= 1)
        except PipelineCancelled as exc:
            caught["exc"] = exc

    try:
        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=180)
        assert not thread.is_alive(), "成品线取消没有返回"
    finally:
        pipeline.optimize_image = real_optimize

    assert "exc" in caught, "取消没有抛 PipelineCancelled"
    assert runs["n"] >= 1, "取消前一张图都没处理，用例没测到关键路径"
    data = json.loads((out_dir / "changelog.json").read_text(encoding="utf-8"))
    assert data["cancelled"] is True

    assert written, "取消前没有任何产物落盘，用例没测到关键路径"
    partial_saved = sum(r.get("saved", 0) for r in caught["exc"].partial_results)
    image_records = [rec for rec in data["records"]
                     if rec["action"] in ("compress", "convert")
                     and rec["src"].startswith("game/pic")]
    # 不重复记账：同一个文件只出现一次（“这里并一次、那边再并一次”会翻倍）
    assert len({rec["src"] for rec in image_records}) == len(image_records), \
        f"同一个文件被记了两次账：{image_records}"
    # 不变量（issue #39 的核心）：产物有 ⇒ 台账有。
    # 旧版只对取消瞬间已完成的 future 拍快照，这里会有产物却查不到记录。
    recorded = {Path(rec["src"]).name for rec in image_records}
    assert recorded == set(written), (
        f"产物与台账对不上：落盘 {sorted(written)}，"
        f"记账 {sorted(recorded)}")
    # 台账与随异常带出的部分结果同口径（不多不少）
    assert data["saved_bytes"] == partial_saved, (
        f"清单 {data['saved_bytes']} 与部分结果 {partial_saved} 对不上")

def test_cancel_does_not_double_count_records(tmp_path):
    """取消时记账只做一次：不能“这里并一次、调用方再并一次”（issue #39）。

    调用方（工程线 _run_batch / 成品线 _aggregate）把自己的记账函数交给
    _run_jobs_or_flush；两边都并一次就会让清单里的记录与 saved_bytes 翻倍。
    """
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    records: list = []
    aggregated: list = []

    def aggregate(results):
        """模拟调用方的记账：返回本批新增的节省字节。"""
        take = 0
        for r in results:
            records.extend(r["records"])
            take += r.get("saved", 0)
        aggregated.append(take)
        return take

    def job(i):
        def fn():
            return _result(10, [ChangeRecord(action="compress",
                                             src=f"{i}.png", detail="x")])
        return (f"j{i}", fn)

    with pytest.raises(PipelineCancelled):
        _run_jobs_or_flush(Progress(), "optimize", [job(i) for i in range(3)],
                           lambda: True, str(out_dir), records, 0,
                           aggregate=aggregate)

    data = json.loads((out_dir / "changelog.json").read_text(encoding="utf-8"))
    # 完成了几条取决于轮询时机，但"记了几次"必须与"完成了几条"严格相等：
    # 重复记账会在这里翻倍（调用方与 helper 各并一次）。
    assert records, "没有任何成果被记账，用例没测到关键路径"
    assert len(data["records"]) == len(records), (
        f"记录数对不上：调用方 {len(records)} 条，清单 {len(data['records'])} 条")
    assert data["saved_bytes"] == 10 * len(records), (
        f"节省字节被重复累加：清单 {data['saved_bytes']}，"
        f"应为 {10 * len(records)}")
    assert sum(aggregated) == 10 * len(records)
    assert aggregated == [10 * len(records)], f"记账函数被调用多次：{aggregated}"
