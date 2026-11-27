"""RPA 记账事务的回归测试（issue #37 / #38）。

口径：封包内资产的收益与改动记录都是**暂记**，只有封包重建成功并
替换掉原包之后才算已落地。四个出口（成功 / 未变小 / 重建失败 / 取消）
都必须让 saved_bytes 与 changelog.json 与磁盘上的产物一致。
"""
from __future__ import annotations

import io
import json
import pickle
import random
import zlib
from pathlib import Path

import pytest                                       # noqa: E402

import rtools.pipeline as pipeline                  # noqa: E402
from rtools.config import OptimizeOptions           # noqa: E402
from rtools.models import ChangeRecord              # noqa: E402
from rtools.pipeline import PipelineCancelled       # noqa: E402


# ---------------------------------------------------------------------------
# 夹具：真实可读的 RPA 封包 + 收益可预测的 PNG
# ---------------------------------------------------------------------------

_KEY = 0x42424242


def _write_rpa(path: Path, assets: dict, filler_count: int = 500) -> None:
    """写一个合法 RPA-3.0（zlib 索引 + 大量共享长前缀的填充条目）。

    填充条目的作用：让索引/格式开销在重建时真实存在。条目足够多时，
    一个字节的资产收益也会被重建开销吃掉，从而触发"未变小"分支。
    """
    placeholder = b"RPA-3.0 " + b"X" * 16 + b" " + b"X" * 8 + b"\n"
    body = bytearray(placeholder)
    entries: dict = {}
    for i in range(filler_count):
        name = f"data/very-long-shared-prefix/chapter-{i:05d}-asset.dat"
        data = f"payload-{i}".encode() * 4
        body += b"Made with Ren'Py."
        offset = len(body)
        body += data
        entries[name] = [(offset ^ _KEY, len(data) ^ _KEY, b"")]
    for name, data in assets.items():
        body += b"Made with Ren'Py."
        offset = len(body)
        body += data
        entries[name] = [(offset ^ _KEY, len(data) ^ _KEY, b"")]
    index_offset = len(body)
    index = zlib.compress(pickle.dumps(entries, 2))
    path.write_bytes(
        b"RPA-3.0 %016x %08x\n" % (index_offset, _KEY)
        + body[len(placeholder):] + index)


def _optimized_png_bytes() -> bytes:
    """已压到最优的 PNG 再补一个冗余字节：同名再压最多只省 1 字节。"""
    from PIL import Image

    pixels = random.Random(42).randbytes(64 * 64 * 3)
    buf = io.BytesIO()
    Image.frombytes("RGB", (64, 64), pixels).save(buf, "PNG", optimize=True)
    return buf.getvalue() + b"\0"


def _compressible_png_bytes() -> bytes:
    """未优化的渐变 PNG：同名压缩能确定省下字节。"""
    from PIL import Image

    buf = io.BytesIO()
    Image.linear_gradient("L").resize((800, 800)).convert("RGB").save(
        buf, "PNG", optimize=False)
    return buf.getvalue()


def _make_dist(tmp_path: Path, image_data: bytes,
               filler_count: int = 500) -> Path:
    dist = tmp_path / "Game-pc"
    game = dist / "game"
    game.mkdir(parents=True)
    _write_rpa(game / "archive.rpa", {"images/bg.png": image_data},
               filler_count=filler_count)
    return dist


def _image_only_options() -> OptimizeOptions:
    opts = OptimizeOptions()
    opts.use_cache = False
    opts.do_audio = False
    opts.do_fonts = False
    return opts


def _changelog(tmp_path: Path) -> dict:
    return json.loads(
        (tmp_path / "out" / "changelog.json").read_text(encoding="utf-8"))


def _rpa_asset_records(changelog: dict) -> list:
    """清单里针对封包内那唯一资源的改动记录。"""
    return [r for r in changelog["records"] if r["src"] == "images/bg.png"]


# ---------------------------------------------------------------------------
# 1) 封包未变小：清单里不能留下"改过"的痕迹
# ---------------------------------------------------------------------------

def test_rpa_unchanged_drops_asset_records(tmp_path):
    """重建后没变小 → 原包不动，资产记录与收益都不进 changelog（#37）。"""
    dist = _make_dist(tmp_path, _optimized_png_bytes())
    before = (dist / "game" / "archive.rpa").read_bytes()

    result = pipeline.run_dist(
        str(dist), _image_only_options(), str(tmp_path / "work"),
        str(tmp_path / "out"))

    changelog = _changelog(tmp_path)
    # 前提：确实走到了"封包重建后未变小"这条分支
    assert any("未变小" in w for w in result["warnings"])
    assert not _rpa_asset_records(changelog), (
        "封包没被替换，资产却仍记为已修改")
    assert not any(r["action"] == "rpa_rebuild" for r in changelog["records"])
    # saved 与实际产物一致：原包一个字节都没变
    assert (Path(result["working_dir"]) / "game" / "archive.rpa"
            ).read_bytes() == before
    assert result["saved_bytes"] == 0
    assert changelog["saved_bytes"] == result["saved_bytes"]


# ---------------------------------------------------------------------------
# 2) 重建失败：同上
# ---------------------------------------------------------------------------

def test_rpa_rebuild_failure_drops_asset_records(tmp_path, monkeypatch):
    """重建抛异常 → 原包保留，资产记录与收益都不进 changelog（#37）。"""
    dist = _make_dist(tmp_path, _optimized_png_bytes())
    before = (dist / "game" / "archive.rpa").read_bytes()

    def boom(src, dst, replacements):
        Path(dst).write_bytes(b"half-written")     # 故意留个半截临时包
        raise OSError("模拟重建失败")

    monkeypatch.setattr(pipeline.rpa, "rebuild_archive", boom)
    result = pipeline.run_dist(
        str(dist), _image_only_options(), str(tmp_path / "work"),
        str(tmp_path / "out"))

    changelog = _changelog(tmp_path)
    assert any("重建失败" in w for w in result["warnings"])
    assert not _rpa_asset_records(changelog)
    assert not any(r["action"] == "rpa_rebuild" for r in changelog["records"])
    working_game = Path(result["working_dir"]) / "game"
    assert (working_game / "archive.rpa").read_bytes() == before
    assert not list(working_game.rglob("*.rtools.tmp"))
    assert result["saved_bytes"] == 0
    assert changelog["saved_bytes"] == 0


# ---------------------------------------------------------------------------
# 3) 取消：资产优化完了但封包没重建，partial 清单不能含这笔收益
# ---------------------------------------------------------------------------

def test_cancel_before_rebuild_excludes_rpa_savings(tmp_path, monkeypatch):
    """资产优化完成、封包尚未重建时取消 → 部分清单不含 RPA 收益（#38）。"""
    dist = _make_dist(tmp_path, _compressible_png_bytes())
    gated = {"open": False}

    def cancel():
        # 取消只在优化批次真正跑完之后才生效，模拟"RPA 资产已优化、
        # 封包尚未重建"这个真实时间点，不靠 sleep 卡时序。
        return gated["open"]

    real_run_jobs = pipeline._run_jobs

    def run_jobs_then_cancel(p, stage, jobs, cancel_fn=None):
        results = real_run_jobs(p, stage, jobs, cancel_fn)
        gated["open"] = True          # 本批成果已就绪，之后立刻取消
        return results

    monkeypatch.setattr(pipeline, "_run_jobs", run_jobs_then_cancel)
    with pytest.raises(PipelineCancelled):
        pipeline.run_dist(
            str(dist), _image_only_options(), str(tmp_path / "work"),
            str(tmp_path / "out"), cancel=cancel)

    changelog = _changelog(tmp_path)
    assert changelog["cancelled"] is True
    assert not _rpa_asset_records(changelog), (
        "封包尚未重建，部分清单却记了 RPA 资产收益")
    assert not any(r["action"] == "rpa_rebuild" for r in changelog["records"])
    assert changelog["saved_bytes"] == 0
    # 暂记收益如实报数，但不参与 saved
    assert changelog["rpa_pending_bytes"] > 0


# ---------------------------------------------------------------------------
# 4) 成功路径不回归：记录与收益都正确
# ---------------------------------------------------------------------------

def test_rpa_success_commits_asset_records(tmp_path):
    """封包重建成功 → 资产记录过账，saved 按封包净变化记账。"""
    dist = _make_dist(tmp_path, _compressible_png_bytes())
    before = (dist / "game" / "archive.rpa").stat().st_size

    result = pipeline.run_dist(
        str(dist), _image_only_options(), str(tmp_path / "work"),
        str(tmp_path / "out"))

    rebuilt = Path(result["working_dir"]) / "game" / "archive.rpa"
    actual_saved = before - rebuilt.stat().st_size
    assert actual_saved > 0

    changelog = _changelog(tmp_path)
    records = _rpa_asset_records(changelog)
    assert len(records) == 1 and records[0]["action"] == "compress"
    assert any(r["action"] == "rpa_rebuild" for r in changelog["records"])
    # 收益按封包净变化记，不与资产收益相加（会重复记账）
    assert result["saved_bytes"] == actual_saved
    assert changelog["saved_bytes"] == actual_saved
    assert "rpa_pending_bytes" not in changelog


# ---------------------------------------------------------------------------
# 账本本身的契约：提交/丢弃互斥，provisional 只报不记
# ---------------------------------------------------------------------------

def test_ledger_commit_and_discard_are_exclusive():
    ledger = pipeline._RpaLedger()
    rec = ChangeRecord(action="compress", src="images/bg.png", detail="x")
    ledger.stage("archive.rpa", [rec], 123)
    assert ledger.provisional() == 123

    out: list = []
    ledger.commit("archive.rpa", out)
    assert [r.src for r in out] == ["images/bg.png"]
    # 暂记收益作废：真实节省由封包净变化单独记，相加会重复记账
    assert ledger.provisional() == 0
    ledger.discard("archive.rpa")       # 幂等：已提交的不会二次过账
    assert ledger.provisional() == 0

    ledger.stage("other.rpa", [rec], 7)
    ledger.discard("other.rpa")
    assert ledger.staged == {}
    assert ledger.provisional() == 0


def test_flush_view_never_publishes_provisional_savings(tmp_path):
    """落清单视图：RPA 结果只挂账，不进 records/saved。"""
    ledger = pipeline._RpaLedger()
    records: list = [ChangeRecord(action="copy", src="dist", detail="工作副本")]
    saved = 500
    flush = pipeline._ChangelogFlush(str(tmp_path), records,
                                     lambda: saved, ledger)
    rpa_rec = ChangeRecord(action="compress", src="images/bg.png", detail="x")
    loose_rec = ChangeRecord(action="compress", src="audio/bgm.ogg", detail="y")

    flush.absorb([
        {"records": [rpa_rec], "saved": 123, "rpa": ("archive.rpa", "x", "y")},
        {"records": [loose_rec], "saved": 20, "rpa": None},
    ])
    flush.flush()

    data = json.loads((tmp_path / "changelog.json").read_text(encoding="utf-8"))
    assert data["saved_bytes"] == 520             # 散落资产照常计入
    assert data["rpa_pending_bytes"] == 123       # 暂记只报数
    assert [r["src"] for r in data["records"]] == ["dist", "audio/bgm.ogg"]
