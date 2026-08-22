"""任务 13 修复项的回归测试（2026-08-23）。

覆盖：
1. check_remap_support 版本/缺失/坏格式四态 + 回调冲突扫描；
2. parse_remap_mapping 坏输入返回 ({}, False)；
3. 成品字符集对 GBK 文本不再丢汉字（read_text_robust 回退）；
4. 工程字符集补扫 .rpyc；
5. 仅被编译脚本（.rpyc）引用的音频不被判无引用；
6. RefIndex 加载/改写均排除注入脚本 rtools_remap.rpy。
"""
from __future__ import annotations

import zlib
from pathlib import Path

from rtools import cleanup, refs
from rtools import remap as remap_mod
from rtools.charset import extract_charset, extract_charset_dist
from rtools.config import CharsetOptions
from rtools.models import AssetInfo, AssetKind


def _make_game(tmp_path: Path, version: str | None = "(8, 5, 0)") -> Path:
    game = tmp_path / "game"
    game.mkdir()
    if version is not None:
        (game / "script_version.txt").write_text(version, encoding="utf-8")
    return game


# ---------------------------------------------------------------------------
# ① check_remap_support：script_version 四态
# ---------------------------------------------------------------------------

def test_check_remap_support_ok(tmp_path):
    game = _make_game(tmp_path, "(8, 5, 0)")
    ok, reason = remap_mod.check_remap_support(game)
    assert ok is True and reason == ""


def test_check_remap_support_old_version(tmp_path):
    game = _make_game(tmp_path, "(7, 4, 0)")
    ok, reason = remap_mod.check_remap_support(game)
    assert ok is False
    assert reason != "" and "8.0" in reason


def test_check_remap_support_missing_version_file(tmp_path):
    game = _make_game(tmp_path, version=None)
    ok, reason = remap_mod.check_remap_support(game)
    assert ok is False and reason != ""


def test_check_remap_support_bad_format(tmp_path):
    game = _make_game(tmp_path, "this is not a version tuple")
    ok, reason = remap_mod.check_remap_support(game)
    assert ok is False and reason != ""


# ---------------------------------------------------------------------------
# ② check_remap_support：游戏自身设置同类回调 → 冲突
# ---------------------------------------------------------------------------

def test_check_remap_support_callback_conflict(tmp_path):
    game = _make_game(tmp_path)
    (game / "loader.rpy").write_text(
        "init python:\n"
        "    config.file_open_callback = my_hook\n", encoding="utf-8")
    ok, reason = remap_mod.check_remap_support(game)
    assert ok is False
    assert reason == "检测到游戏自身设置了文件加载回调，remap 可能冲突"


def test_check_remap_support_ignores_own_injected_script(tmp_path):
    """本工具注入的脚本本身含回调字样，不应触发冲突误报。"""
    game = _make_game(tmp_path)
    injected = remap_mod.build_remap_script({"a.png": "a.webp"})
    (game / remap_mod.REMAP_SCRIPT_NAME).write_text(injected, encoding="utf-8")
    ok, reason = remap_mod.check_remap_support(game)
    assert ok is True and reason == ""


# ---------------------------------------------------------------------------
# ③ parse_remap_mapping：坏输入一律 ({}, False)
# ---------------------------------------------------------------------------

def test_parse_remap_mapping_bad_inputs():
    cases = [
        "随便一段文本",                               # 缺标记
        "_renpyslim_remap = 没有花括号",              # 有标记无花括号
        '_renpyslim_remap = {"a": }',                # JSON 语法错
        '_renpyslim_remap = [1, 2, 3]',              # 非 dict
        "",
    ]
    for text in cases:
        mapping, ok = remap_mod.parse_remap_mapping(text)
        assert mapping == {} and ok is False, text


def test_parse_remap_mapping_good_input():
    script = remap_mod.build_remap_script({"images/a.png": "images/a.webp"})
    mapping, ok = remap_mod.parse_remap_mapping(script)
    assert ok is True and mapping == {"images/a.png": "images/a.webp"}


# ---------------------------------------------------------------------------
# ④ 成品字符集：GBK 编码文本不再丢汉字
# ---------------------------------------------------------------------------

def test_dist_charset_keeps_gbk_hanzi(tmp_path):
    (tmp_path / "notes.txt").write_bytes("你好，汉字不能丢".encode("gb18030"))
    chars, _ = extract_charset_dist(str(tmp_path), CharsetOptions())
    for ch in "你好汉字不能丢":
        assert ch in chars, ch


# ---------------------------------------------------------------------------
# 工程字符集：补扫 .rpyc
# ---------------------------------------------------------------------------

def test_project_charset_scans_rpyc(tmp_path):
    (tmp_path / "script.rpyc").write_bytes(
        zlib.compress("独有汉字来源编译脚本".encode()))
    chars, _ = extract_charset(str(tmp_path), CharsetOptions())
    for ch in "独有汉字来源编译脚本":
        assert ch in chars, ch


# ---------------------------------------------------------------------------
# ⑤ 仅被 rpyc 引用的音频不被判无引用
# ---------------------------------------------------------------------------

def _fake_rpyc(text: str) -> bytes:
    """旧格式 rpyc：整体 zlib 压缩（与 read_rpyc_text 的 legacy 分支对齐）。"""
    return zlib.compress(text.encode())


def test_unused_detection_respects_rpyc_refs(tmp_path):
    cleanup._RPYC_TEXT_CACHE.clear()
    game = tmp_path / "game"
    game.mkdir()
    # 明文脚本里完全没有音频引用
    (game / "script.rpy").write_text('e "没有提到任何音频"\n', encoding="utf-8")
    # 编译脚本里有引用（无源码成品的典型形态）
    (game / "script.rpyc").write_bytes(
        _fake_rpyc('play sound "audio/hit.ogg"'))

    ref_index = refs.RefIndex(str(game))
    assets = [
        AssetInfo(path=str(game / "audio" / "hit.ogg"), rel="audio/hit.ogg",
                  kind=AssetKind.AUDIO, size=10),
        AssetInfo(path=str(game / "audio" / "ghost.ogg"), rel="audio/ghost.ogg",
                  kind=AssetKind.AUDIO, size=10),
    ]
    unused = cleanup.find_unused_assets(assets, ref_index)
    assert "audio/hit.ogg" not in unused      # rpyc 兜底命中
    assert "audio/ghost.ogg" in unused        # 真没人引用的仍要标记


# ---------------------------------------------------------------------------
# RefIndex 排除注入脚本 rtools_remap.rpy
# ---------------------------------------------------------------------------

def test_refindex_excludes_remap_script(tmp_path):
    game = tmp_path / "game"
    game.mkdir()
    (game / "script.rpy").write_text('play sound "audio/a.ogg"\n',
                                     encoding="utf-8")
    injected = remap_mod.build_remap_script({"images/x.png": "images/x.webp"})
    (game / remap_mod.REMAP_SCRIPT_NAME).write_text(injected, encoding="utf-8")

    ref_index = refs.RefIndex(str(game))
    # 注入脚本不进索引：里面含 images/x.png 键，不应产生引用命中
    assert remap_mod.REMAP_SCRIPT_NAME not in ref_index.files
    assert ref_index.find("images/x.png") == []
    # 改写也碰不到注入脚本
    records = ref_index.rewrite({"images/x.png": "images/x.webp"})
    assert records == []
    # 正常脚本不受影响
    assert ref_index.find("audio/a.ogg")
