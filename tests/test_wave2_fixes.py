"""第二波修复回归测试：优化器三态、取消时效、原子写、封包撞名防护。

每条对应任务 #16 修复的隐蔽缺陷，防止将来重构时把坑再挖出来。
"""
from __future__ import annotations

import time
import zipfile
from pathlib import Path

import pytest

from rtools import procutil
from rtools.image_optimizer import OptimizeResult, optimize_image
from rtools.models import Progress
from rtools.pipeline import (
    PipelineCancelled,
    PipelineError,
    _run_jobs,
    _safe_job,
    _write_json,
)

# ---------------------------------------------------------------------------
# 优化器三态契约（OptimizeResult）
# ---------------------------------------------------------------------------

def test_optimize_result_truthiness_contract():
    """三种 status 的真值行为契约：只有 ok 为真。"""
    ok = OptimizeResult(status="ok", path="x", old_size=10, new_size=5)
    skipped = OptimizeResult(status="skipped", reason="已是最优")
    failed = OptimizeResult(status="failed", reason="boom")
    assert bool(ok) is True
    assert bool(skipped) is False
    assert bool(failed) is False
    # 记账以 status 字段为准（不依赖真值）：假值里也分得清
    assert skipped["status"] == "skipped"
    assert failed["status"] == "failed"


def test_old_caller_pattern_ok_and_skipped():
    """apk.py 式旧调用方（`if res:` + res['new_size']）不回归：
    ok 时按成功记账，skipped 时安静跳过——语义与旧版 Optional[dict]
    （成功字典/None）完全一致，不炸 TypeError、不误判成功。
    """

    def legacy_caller(res):
        # 模拟 apk.py 主循环里的既有写法
        saved = 0
        if res:
            saved += res["old_size"] - res["new_size"]
        return saved

    ok = OptimizeResult(status="ok", path="x", old_size=100, new_size=60,
                        converted=False)
    skipped = OptimizeResult(status="skipped", reason="已是最优")
    assert legacy_caller(ok) == 40
    assert legacy_caller(skipped) == 0
    assert legacy_caller(None) == 0      # 旧契约的 None 也不能炸


def test_optimize_image_unsupported_ext_skipped(tmp_path):
    """PIL 能开但不在白名单（如 BMP）→ skipped，不是 failed。"""
    from PIL import Image
    p = tmp_path / "t.bmp"
    Image.new("RGB", (10, 10), (1, 2, 3)).save(p, "BMP")
    before = p.read_bytes()
    res = optimize_image(str(p), str(p), quality=85)
    assert res["status"] == "skipped"
    assert not res
    assert "不支持" in res["reason"]
    assert p.read_bytes() == before      # 目标文件绝不能被动过


def test_optimize_image_missing_source_failed(tmp_path):
    """源文件不存在是真错误 → failed（不是 skipped）。"""
    res = optimize_image(str(tmp_path / "nope.png"),
                         str(tmp_path / "nope.png"), quality=85)
    assert res["status"] == "failed"
    assert not res


def test_video_refuse_unknown_codec(monkeypatch, tmp_path):
    """webm/ogv 探测返回 None（编码未知）不再盲编，保守归 skipped。"""
    import rtools.video_optimizer as vo
    monkeypatch.setattr(vo, "probe_video_codec", lambda p: None)
    monkeypatch.setattr(vo, "find_ffmpeg", lambda: "fake-ffmpeg")
    for name in ("v.webm", "v.ogv", "v.mp4"):
        src = tmp_path / name
        src.write_bytes(b"x")
        res = vo.compress_video(str(src), str(src))
        assert res["status"] == "skipped", name
        assert "未知" in res["reason"]


# ---------------------------------------------------------------------------
# _write_json 原子写
# ---------------------------------------------------------------------------

def test_write_json_atomic(tmp_path):
    """先写临时文件再 os.replace：内容正确、不留临时残骸。"""
    target = tmp_path / "out" / "report.json"
    _write_json(target, {"k": "值"})
    assert '"k": "值"' in target.read_text(encoding="utf-8")
    assert not list(tmp_path.rglob("*.tmp"))


def test_write_json_failure_leaves_no_tmp(tmp_path):
    """序列化失败时临时文件必须清掉，不能留半截环。"""
    target = tmp_path / "bad.json"

    class NotSerializable:
        pass

    with pytest.raises(TypeError):
        _write_json(target, {"bad": NotSerializable()})
    assert not target.exists()
    assert not list(tmp_path.rglob("*.tmp"))


# ---------------------------------------------------------------------------
# _safe_job 异常归因 + _run_jobs 取消时效
# ---------------------------------------------------------------------------

def test_safe_job_attributes_exception_to_kind():
    """job 内异常不再被吞掉不计账：按类型归因计 failed。"""
    def boom():
        raise ValueError("炸了")

    label, wrapped = _safe_job("audio", ("a.ogg", boom))
    assert label == "a.ogg"
    r = wrapped()
    assert r["failed"] == "audio"
    assert r["skipped"] is None
    assert "炸了" in r["exception"]


def test_run_jobs_cancel_within_seconds():
    """长任务在跑时取消也要秒级生效（短超时轮询），
    不能像旧版那样阻塞在 as_completed 上干等。
    用真实子进程模拟 ffmpeg：取消时 kill_children 杀进程，
    线程才能真正退出（纯 sleep 的线程杀不掉，不符合真实场景）。"""
    import sys

    def long_job():
        # 被杀后 run_quiet 正常返回（返回码非 0），无需吞异常；
        # 真起不来就让它报错，测试环境不该走到那步。
        procutil.run_quiet([sys.executable, "-c",
                            "import time; time.sleep(60)"],
                           timeout=120)
        return {"records": [], "saved": 0}

    jobs = [("slow", long_job)]
    started = time.monotonic()
    with pytest.raises(PipelineCancelled):
        # 第一次轮询间隙就喊停；给一个极短宽限避免和提交竞态
        _run_jobs(Progress(), "test", jobs,
                  cancel=lambda: time.monotonic() - started > 0.3)
    assert time.monotonic() - started < 10, "取消没有秒级生效"


def test_run_jobs_exception_counts_failed():
    """任务抛异常时 _run_jobs 兜底也计入失败（不再静默丢弃）。"""
    def boom():
        raise ValueError("炸了")

    results = _run_jobs(Progress(), "test", [("b", boom)])
    assert len(results) == 1
    assert results[0]["failed"]


# ---------------------------------------------------------------------------
# run_dist_smart：解压预清理 / 改名占用防护
# ---------------------------------------------------------------------------

def _make_dist_zip(zip_path: Path):
    """造一个最小成品压缩包的测试输入。"""
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("MyGame/game/options.rpy", "define x = 1\n")
        zf.writestr("MyGame/game/script.rpy", 'label start:\n    "hi"\n')


def test_run_dist_smart_cleans_stale_extract_dir(tmp_path):
    """解压前必须删掉上次崩溃残留的同名解压目录。"""
    from rtools.config import OptimizeOptions
    from rtools.pipeline import run_dist_smart

    src_zip = tmp_path / "Game.zip"
    _make_dist_zip(src_zip)
    work = tmp_path / "work"
    work.mkdir()
    # 预置一个脏残留（上次崩溃没清掉）
    stale = work / "Game-解压"
    stale.mkdir()
    (stale / "STALE_MARKER.txt").write_text("old", encoding="utf-8")

    opts = OptimizeOptions()
    run_dist_smart(str(src_zip), opts, str(work), str(tmp_path / "out"))
    # 残留被清掉（且回包成功后新解压目录也不留）
    assert not (stale / "STALE_MARKER.txt").exists()
    assert not stale.exists()


def test_run_dist_smart_rename_occupied_gives_clear_error(tmp_path, monkeypatch):
    """改名被占用时给明确错误（含占用提示），不能裸抛 OSError。"""
    from rtools.config import OptimizeOptions
    from rtools.pipeline import run_dist_smart

    src_zip = tmp_path / "Game.zip"
    _make_dist_zip(src_zip)
    work = tmp_path / "work"
    work.mkdir()

    real_rename = Path.rename

    def fake_rename(self, target):
        # 只在"工作目录 -> 规范名"这一步模拟占用
        if Path(target).name == "MyGame":
            raise OSError(32, "另一个程序正在使用")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", fake_rename)
    opts = OptimizeOptions()
    with pytest.raises(PipelineError, match="占用"):
        run_dist_smart(str(src_zip), opts, str(work), str(tmp_path / "out"))
