"""第二批功能回归测试：垃圾清理、废资源检测、重复检测、缺字报告、验证器兜底。"""
from __future__ import annotations

from rtools.charset import find_missing_glyphs            # noqa: E402
from rtools.cleanup import (clean_junk, find_duplicates,  # noqa: E402
                            find_unused_assets, quarantine_files)
from rtools.models import AssetInfo, AssetKind            # noqa: E402
from rtools.refs import RefIndex                          # noqa: E402
from rtools.verifier import lint_project                  # noqa: E402


def _asset(path: Path, rel: str, kind: AssetKind) -> AssetInfo:
    return AssetInfo(path=str(path), rel=rel, kind=kind, size=path.stat().st_size)


# ---------------------------------------------------------------------------
# 垃圾清理
# ---------------------------------------------------------------------------

def test_clean_junk_only_removes_regenerable(tmp_path):
    (tmp_path / "saves").mkdir(); (tmp_path / "saves" / "1.save").write_bytes(b"x" * 100)
    (tmp_path / "game" / "cache").mkdir(parents=True)
    (tmp_path / "game" / "cache" / "bytecode.rpyb").write_bytes(b"y" * 200)
    (tmp_path / "errors.txt").write_bytes(b"e" * 50)
    (tmp_path / "game" / "script.rpy").write_text("keep me", encoding="utf-8")
    (tmp_path / "game" / "bg.png").write_bytes(b"png")

    res = clean_junk(str(tmp_path))
    assert res["freed_bytes"] == 350
    assert not (tmp_path / "saves").exists()
    assert not (tmp_path / "game" / "cache").exists()
    assert not (tmp_path / "errors.txt").exists()
    # 正经资源与脚本绝不能被碰
    assert (tmp_path / "game" / "script.rpy").exists()
    assert (tmp_path / "game" / "bg.png").exists()


# ---------------------------------------------------------------------------
# 废资源检测
# ---------------------------------------------------------------------------

def test_unused_detection_marks_audio_not_images(tmp_path):
    game = tmp_path / "game"
    game.mkdir()
    (game / "script.rpy").write_text('play music "audio/bgm.ogg"\n', encoding="utf-8")
    (game / "audio").mkdir()
    (game / "audio" / "bgm.ogg").write_bytes(b"1")
    (game / "audio" / "unused.wav").write_bytes(b"2")
    (game / "orphan.png").write_bytes(b"3")     # 无引用图片：不许标记

    assets = [
        _asset(game / "audio" / "bgm.ogg", "audio/bgm.ogg", AssetKind.AUDIO),
        _asset(game / "audio" / "unused.wav", "audio/unused.wav", AssetKind.AUDIO),
        _asset(game / "orphan.png", "orphan.png", AssetKind.IMAGE),
    ]
    idx = RefIndex(str(game))
    unused = find_unused_assets(assets, idx)
    assert unused == ["audio/unused.wav"]


def test_quarantine_moves_not_deletes(tmp_path):
    (tmp_path / "a.ogg").write_bytes(b"x")
    moved = quarantine_files(str(tmp_path), ["a.ogg", "missing.ogg"])
    assert moved == ["a.ogg"]
    assert not (tmp_path / "a.ogg").exists()
    assert (tmp_path / "_rtools_quarantine" / "a.ogg").exists()


# ---------------------------------------------------------------------------
# 重复文件检测
# ---------------------------------------------------------------------------

def test_duplicates_grouping(tmp_path):
    (tmp_path / "a.png").write_bytes(b"SAME" * 10)
    (tmp_path / "b.png").write_bytes(b"SAME" * 10)
    (tmp_path / "c.png").write_bytes(b"DIFF" * 10)
    assets = [_asset(tmp_path / n, n, AssetKind.IMAGE)
              for n in ("a.png", "b.png", "c.png")]
    dups = find_duplicates(assets)
    assert len(dups) == 1
    assert dups[0]["files"] == ["a.png", "b.png"]


# ---------------------------------------------------------------------------
# 缺字报告
# ---------------------------------------------------------------------------

def test_missing_glyphs(tmp_path):
    from test_core import _make_tiny_font
    font = tmp_path / "t.ttf"
    _make_tiny_font(font)          # 只有 A、B 两个字形
    missing = find_missing_glyphs(str(font), {"A", "中", "文"})
    assert missing == ["中", "文"]
    # 坏路径不崩，返回空
    assert find_missing_glyphs(str(tmp_path / "nope.ttf"), {"A"}) == []


# ---------------------------------------------------------------------------
# 验证器兜底
# ---------------------------------------------------------------------------

def test_lint_missing_sdk_graceful(tmp_path):
    res = lint_project(str(tmp_path), str(tmp_path))   # 目录下没有 renpy.exe
    assert res["ran"] is False and res["ok"] is False
