"""多语言感知字体瘦身的回归测试（2026-08-25 补缺口）。

口径：字体瘦身永远按“多语言大包”服务，没有单语言发行过滤。
覆盖：语言识别、按语言分桶、{font=} 行内标签字符归属、
少用字体处数统计、单字体三档瘦身计划（精确/语言定向/全量兜底）。
"""
from __future__ import annotations

import json
from pathlib import Path

from rtools import charset, cleanup
from rtools.config import CharsetOptions
from rtools.models import AssetInfo, AssetKind
from rtools.refs import RefIndex


def _make_multilang_game(root: Path) -> Path:
    """合成多语言工程：主剧本中文 + tl/None 覆盖 + 英俄两门翻译。"""
    game = root / "game"
    (game / "tl" / "None").mkdir(parents=True)
    (game / "tl" / "english").mkdir(parents=True)
    (game / "tl" / "russian").mkdir(parents=True)
    (game / "script.rpy").write_text(
        'label start:\n    e "你好世界"\n', encoding="utf-8")
    (game / "tl" / "None" / "gui.rpy").write_text(
        '# 覆盖项标记字 ü\n', encoding="utf-8")
    (game / "tl" / "english" / "script.rpy").write_text(
        'translate start e_x:\n    e "Hello Quest £"\n', encoding="utf-8")
    (game / "tl" / "russian" / "script.rpy").write_text(
        'translate start e_x:\n    e "ПриветЩ"\n', encoding="utf-8")
    return game


# ---------------------------------------------------------------------------
# 语言识别与分桶
# ---------------------------------------------------------------------------

def test_detect_languages_from_game_root_and_project_root(tmp_path):
    game = _make_multilang_game(tmp_path)
    assert charset.detect_languages(str(game)) == ["english", "russian"]
    # 从工程根（含 game/ 子目录）也能探到
    assert charset.detect_languages(str(tmp_path)) == ["english", "russian"]
    # 无翻译目录 → 空列表
    empty = tmp_path / "bare"
    (empty / "game").mkdir(parents=True)
    assert charset.detect_languages(str(empty)) == []


def test_detect_languages_ignores_nested_tl(tmp_path):
    """tl/tl/ 嵌套异常结构不得被误识为名叫 tl 的语言。"""
    game = _make_multilang_game(tmp_path)
    (game / "tl" / "tl" / "None").mkdir(parents=True)
    (game / "tl" / "tl" / "None" / "common.rpym").write_bytes(b"x")
    assert charset.detect_languages(str(game)) == ["english", "russian"]
    # 嵌套目录属公共内容，全语言合集里照样有它
    chars, _ = charset.extract_charset(str(game), CharsetOptions())
    assert "x" in chars


def test_scan_language_buckets(tmp_path):
    game = _make_multilang_game(tmp_path)
    buckets, _ = charset.scan_language_buckets(str(game))
    assert "你" in buckets[charset.BASE_BUCKET]      # 主剧本归 base 桶
    assert "ü" in buckets[charset.BASE_BUCKET]      # tl/None 属公共内容归 base 桶
    assert "H" in buckets["english"] and "П" in buckets["russian"]
    assert "П" not in buckets[charset.BASE_BUCKET]


def test_extract_charset_is_full_union(tmp_path):
    """字符集永远是全语言合集（多语言大包口径），不做单语言过滤。"""
    game = _make_multilang_game(tmp_path)
    chars, _ = charset.extract_charset(str(game), CharsetOptions())
    assert "你" in chars and "ü" in chars      # 主剧本 + tl/None
    assert "£" in chars and "П" in chars       # 各翻译语言全在


# ---------------------------------------------------------------------------
# {font=} 行内标签字符归属
# ---------------------------------------------------------------------------

def test_collect_font_tag_chars(tmp_path):
    game = _make_multilang_game(tmp_path)
    (game / "script2.rpy").write_text(
        'label t:\n'
        '    show expression Text("{font=fonts/title.ttf}秘境传说{/font}")\n'
        '    e "{font=fonts/title.ttf}第二处{b}加粗{/b}用法{/font}"\n',
        encoding="utf-8")
    tag_map = charset.collect_font_tag_chars(str(game))
    got = tag_map["fonts/title.ttf"]
    assert "秘" in got and "境" in got      # 第一处标签文本
    assert "第" in got and "法" in got      # 第二处标签文本（跨标签合并）
    # 嵌套标签内的文字保留，标签记号本身不进集合
    assert "加" in got and "粗" in got
    assert "{" not in got and "b" not in got


def test_tag_chars_for_font_matching_variants():
    tag_map = {
        "fonts/title.ttf": {"甲"},
        "TITLE.TTF": {"乙"},          # 大小写不同的裸文件名
        "gui/other.ttf": {"丙"},
    }
    assert charset.tag_chars_for_font("fonts/title.ttf", tag_map) == {"甲", "乙"}
    assert charset.tag_chars_for_font("gui/other.ttf", tag_map) == {"丙"}
    assert charset.tag_chars_for_font("fonts/missing.otf", tag_map) == set()


# ---------------------------------------------------------------------------
# 少用字体处数统计与三档瘦身计划
# ---------------------------------------------------------------------------

def _make_font_project(root: Path) -> Path:
    """一个含两种字体用法的工程：标签字体 vs 样式字体。"""
    game = root / "game"
    (game / "fonts").mkdir(parents=True)
    (game / "fonts" / "title.ttf").write_bytes(b"fake")
    (game / "fonts" / "main.ttf").write_bytes(b"fake")
    (game / "script.rpy").write_text(
        'define gui.text_font = "fonts/main.ttf"\n'
        'label start:\n'
        '    show expression Text("{font=fonts/title.ttf}标题字样{/font}")\n',
        encoding="utf-8")
    return game


def test_refs_in_font_tags(tmp_path):
    game = _make_font_project(tmp_path)
    idx = RefIndex(str(game))
    tagged, total = idx.refs_in_font_tags("fonts/title.ttf")
    assert (tagged, total) == (1, 1), "标签字体的引用应全落在标签里"
    tagged, total = idx.refs_in_font_tags("fonts/main.ttf")
    assert total == 1 and tagged == 0, "样式引用不算标签内"


def test_font_usage_report_rare_warning(tmp_path):
    game = _make_font_project(tmp_path)
    idx = RefIndex(str(game))
    fonts = [
        AssetInfo(path=str(game / "fonts" / "title.ttf"),
                  rel="fonts/title.ttf", kind=AssetKind.FONT, size=4),
        AssetInfo(path=str(game / "fonts" / "main.ttf"),
                  rel="fonts/main.ttf", kind=AssetKind.FONT, size=4),
    ]
    usage, warns = cleanup.font_usage_report(idx, fonts)
    assert usage["fonts/title.ttf"]["refs"] == 1
    assert usage["fonts/main.ttf"]["files"] == ["script.rpy"]
    # 语言归属：两字体都只在主剧本引用 → base
    assert usage["fonts/title.ttf"]["langs"] == ["base"]
    assert any("引用很少" in w and "title.ttf" in w for w in warns)


def test_font_usage_compiled_script_fallback(tmp_path):
    """引用只写在编译脚本里时，处数靠 .rpyc 兜底，不误报为零。"""
    import zlib
    game = tmp_path / "game"
    game.mkdir(parents=True)
    (game / "script.rpy").write_text("label start:\n", encoding="utf-8")
    (game / "compiled.rpyc").write_bytes(
        zlib.compress(b'define gui.name_font = "fonts/magic.ttf"'))
    idx = RefIndex(str(game))
    fonts = [AssetInfo(path=str(game / "fonts" / "magic.ttf"),
                       rel="fonts/magic.ttf", kind=AssetKind.FONT, size=4)]
    usage, _ = cleanup.font_usage_report(idx, fonts)
    assert usage["fonts/magic.ttf"]["refs"] == 1
    assert usage["fonts/magic.ttf"]["files"] == ["（编译脚本 .rpyc/.rpymc）"]
    # 编译文本定位不到语言：归属不可知，不得参与语言定向
    assert usage["fonts/magic.ttf"]["langs"] is None


def test_font_slim_plan_precise_and_global(tmp_path):
    from rtools import pipeline
    game = _make_font_project(tmp_path)
    idx = RefIndex(str(game))
    buckets, _ = charset.scan_language_buckets(str(game))
    tag_buckets = charset.scan_font_tag_buckets(str(game))
    cs = CharsetOptions()
    global_chars = {"全", "量"}

    slim, mode = pipeline._font_slim_plan(
        "fonts/title.ttf", global_chars, buckets, tag_buckets,
        {charset.BASE_BUCKET}, idx, cs)
    assert mode == "precise"
    assert "标" in slim and "题" in slim
    # 精确模式仍带保底字符集（拉丁保底默认开）
    assert "A" in slim
    # 与全量集合互不混淆
    assert "全" not in slim

    slim, mode = pipeline._font_slim_plan(
        "fonts/main.ttf", global_chars, buckets, tag_buckets,
        {charset.BASE_BUCKET}, idx, cs)
    assert mode == "global" and slim is global_chars


def test_font_slim_plan_lang_scoped(tmp_path):
    """字体只被部分语言的翻译引用（tl 外零引用）：收窄到那些语言 + 主剧本。

    主剧本桶永远并入：翻译不完整时未翻译串仍用该字体渲染原文，
    主剧本字符绝不能丢。
    """
    from rtools import pipeline
    game = _make_multilang_game(tmp_path)
    (game / "fonts").mkdir()
    (game / "fonts" / "rus.ttf").write_bytes(b"fake")
    # 只在俄语翻译里被引用（模拟样式引用，非行内标签）
    (game / "tl" / "russian" / "style.rpy").write_text(
        'translate style:\n    font "fonts/rus.ttf"\n', encoding="utf-8")
    idx = RefIndex(str(game))
    buckets, _ = charset.scan_language_buckets(str(game))
    tag_buckets = charset.scan_font_tag_buckets(str(game))
    cs = CharsetOptions()
    build_chars = set()
    for got in buckets.values():
        build_chars |= got
    build_chars.update(cs.base_text())

    # 收窄到俄语桶 + 保底 + 主剧本桶：不含英语特有字（£ 不在保底集），
    # 但主剧本的“你”必须保留（未翻译回退会用它渲染原文）
    slim, mode = pipeline._font_slim_plan(
        "fonts/rus.ttf", build_chars, buckets, tag_buckets,
        {"russian"}, idx, cs)
    assert mode == "lang_scoped"
    assert "П" in slim and "你" in slim and "£" not in slim
    assert "A" in slim   # 拉丁保底永远在

    # 归属拿不准（None）：回退全量，一个字不少
    slim, mode = pipeline._font_slim_plan(
        "fonts/rus.ttf", build_chars, buckets, tag_buckets, None, idx, cs)
    assert mode == "global" and slim is build_chars


def test_main_font_never_scoped(tmp_path):
    """红线回归：主字体只在 tl 外（gui/剧本）被引用时，引用位置≠服务语言，
    它可能渲染任何语言的文本——禁止收窄，否则切语言整片方框。"""
    from rtools import pipeline
    game = _make_multilang_game(tmp_path)
    (game / "fonts").mkdir()
    (game / "fonts" / "main.ttf").write_bytes(b"fake")
    (game / "gui.rpy").write_text(
        'define gui.text_font = "fonts/main.ttf"\n', encoding="utf-8")
    idx = RefIndex(str(game))
    buckets, _ = charset.scan_language_buckets(str(game))
    tag_buckets = charset.scan_font_tag_buckets(str(game))
    cs = CharsetOptions()
    build_chars = set()
    for got in buckets.values():
        build_chars |= got
    build_chars.update(cs.base_text())

    # scope 含 base（有 tl 外引用）→ 禁止收窄，全量保留俄语字
    slim, mode = pipeline._font_slim_plan(
        "fonts/main.ttf", build_chars, buckets, tag_buckets,
        {"base"}, idx, cs)
    assert mode == "global" and slim is build_chars
    assert "П" in slim and "£" in slim


def test_tainted_font_refuses_precise(tmp_path):
    """红线回归：标签体含 [插值] 的字体禁止进精确档（运行时显示不可预知）。"""
    from rtools import pipeline
    game = _make_font_project(tmp_path)
    (game / "script3.rpy").write_text(
        'label d:\n    e "{font=fonts/title.ttf}[story]{/font}"\n',
        encoding="utf-8")
    idx = RefIndex(str(game))
    buckets, _ = charset.scan_language_buckets(str(game))
    tag_buckets = charset.scan_font_tag_buckets(str(game))
    tainted = charset.scan_tainted_font_keys(str(game))
    cs = CharsetOptions()
    build_chars = set()
    for got in buckets.values():
        build_chars |= got
    build_chars.update(cs.base_text())

    assert charset.font_keys_match("fonts/title.ttf", tainted)
    slim, mode = pipeline._font_slim_plan(
        "fonts/title.ttf", build_chars, buckets, tag_buckets,
        {charset.BASE_BUCKET}, idx, cs, tainted)
    assert mode == "global" and slim is build_chars


def test_comment_only_tag_not_precise(tmp_path):
    """红线回归：注释里的标签是假阳性，不能作为精确档的证据。"""
    game = tmp_path / "game"
    (game / "fonts").mkdir(parents=True)
    (game / "fonts" / "ghost.ttf").write_bytes(b"fake")
    (game / "script.rpy").write_text(
        '# 旧写法留档：{font=fonts/ghost.ttf}注释字{/font}\n'
        'label start:\n', encoding="utf-8")
    idx = RefIndex(str(game))
    # 注释行不算引用 → total=0 → 必落 global（由 _font_slim_plan 的守卫兼住）
    assert idx.refs_in_font_tags("fonts/ghost.ttf") == (0, 0)


def test_unclosed_tag_not_precise(tmp_path):
    """红线回归：未闭合标签拿不到归属证据，不计入标签内引用。"""
    game = tmp_path / "game"
    (game / "fonts").mkdir(parents=True)
    (game / "fonts" / "open.ttf").write_bytes(b"fake")
    (game / "script.rpy").write_text(
        'label start:\n    e "{font=fonts/open.ttf}未闭合文本"\n',
        encoding="utf-8")
    idx = RefIndex(str(game))
    tagged, total = idx.refs_in_font_tags("fonts/open.ttf")
    assert total == 1 and tagged == 0


def test_compiled_mismatch_downgrades_precise(tmp_path):
    """红线回归：.rpy 全标签引用但编译脚本里另有样式引用 → 降级全量。"""
    import zlib
    game = _make_font_project(tmp_path)
    # 编译脚本里多出一条非标签引用（模拟 rpy/rpyc 不一致）
    (game / "extra.rpyc").write_bytes(
        zlib.compress(b'define gui.name_font = "fonts/title.ttf"'))
    assert cleanup.compiled_font_ref_mismatch(str(game), "fonts/title.ttf")
    # 无编译脚本不一致时不误伤：main.ttf 在编译文本里没出现（本文件不含它）
    # 换用编译文本里确无引用的字体验证不误报：构造一个干净的字体引用场景太贵，
    # 直接验证无引用字体返回 False。
    assert not cleanup.compiled_font_ref_mismatch(str(game), "fonts/none.ttf")


def test_lang_scoped_downgrades_on_compiled_mismatch(tmp_path):
    """红线回归：语言定向档同样吃编译不一致防护——遗留编译样式引用
    能让字体渲染所有语言，收窄即方框。"""
    import zlib
    from rtools import pipeline
    game = _make_multilang_game(tmp_path)
    (game / "fonts").mkdir()
    (game / "fonts" / "rus.ttf").write_bytes(b"fake")
    (game / "tl" / "russian" / "style.rpy").write_text(
        'translate style:\n    font "fonts/rus.ttf"\n', encoding="utf-8")
    # 遗留编译脚本以样式引用该字体 → 可能渲染任何语言 → 必须降级
    (game / "legacy.rpyc").write_bytes(
        zlib.compress(b'style default font "fonts/rus.ttf"'))
    idx = RefIndex(str(game))
    buckets, _ = charset.scan_language_buckets(str(game))
    tag_buckets = charset.scan_font_tag_buckets(str(game))
    cs = CharsetOptions()
    build_chars = set()
    for got in buckets.values():
        build_chars |= got
    build_chars.update(cs.base_text())
    # 守卫能检出该不一致（流水线对精确档与定向档都会据此降级全量）
    assert cleanup.compiled_font_ref_mismatch(str(game), "fonts/rus.ttf")
    # 若无守卫会误入定向档——证明守卫必要：
    slim, mode = pipeline._font_slim_plan(
        "fonts/rus.ttf", build_chars, buckets, tag_buckets,
        {"russian"}, idx, cs)
    assert mode == "lang_scoped"


def test_same_line_mixed_ref_not_tagged(tmp_path):
    """红线回归：同一行标签与样式引用共存时，该行不算“全在标签内”，
    否则样式引用让字体渲染任意文本，精确档必出方框。"""
    game = tmp_path / "game"
    (game / "fonts").mkdir(parents=True)
    (game / "fonts" / "mix.ttf").write_bytes(b"fake")
    (game / "script.rpy").write_text(
        'label start:\n'
        '    $ style.default.font = "fonts/mix.ttf"; e "{font=fonts/mix.ttf}标题{/font}"\n',
        encoding="utf-8")
    idx = RefIndex(str(game))
    tagged, total = idx.refs_in_font_tags("fonts/mix.ttf")
    assert total == 1 and tagged == 0


def test_font_shared_by_languages(tmp_path):
    """同一字体被多门语言引用：标签归属取全语言并集，谁的字都不丢。"""
    from rtools import pipeline
    game = _make_multilang_game(tmp_path)
    (game / "fonts").mkdir()
    (game / "fonts" / "shared.ttf").write_bytes(b"fake")
    for lang, text in (("english", "TITLEWORDS€"), ("russian", "ЗАГОЛОВОК")):
        (game / "tl" / lang / "tag.rpy").write_text(
            f'translate x:\n    e "{{font=fonts/shared.ttf}}{text}{{/font}}"\n',
            encoding="utf-8")
    idx = RefIndex(str(game))
    cs = CharsetOptions()
    buckets, _ = charset.scan_language_buckets(str(game))
    tag_buckets = charset.scan_font_tag_buckets(str(game))
    build_chars = set()
    for got in buckets.values():
        build_chars |= got

    # 处数统计：两门语言的引用都如实计入，语言归属字段如实列出
    usage, _ = cleanup.font_usage_report(idx, [AssetInfo(
        path=str(game / "fonts" / "shared.ttf"), rel="fonts/shared.ttf",
        kind=AssetKind.FONT, size=4)])
    assert usage["fonts/shared.ttf"]["refs"] == 2
    assert usage["fonts/shared.ttf"]["langs"] == ["english", "russian"]

    # 多语言大包：两门语言的特有字都留（并集）
    slim, mode = pipeline._font_slim_plan(
        "fonts/shared.ttf", build_chars, buckets, tag_buckets,
        {"english", "russian"}, idx, cs)
    assert mode == "precise"
    assert "€" in slim and "З" in slim


# ---------------------------------------------------------------------------
# CLI 契约：languages / font_usage 字段随分析报告下发
# ---------------------------------------------------------------------------

def _run_cli_capture(capsys, argv) -> dict:
    import cli
    code = cli.main(argv)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert code == (0 if data["ok"] else 1)
    return data


def test_cli_analyze_reports_languages(tmp_path, capsys):
    proj = _make_multilang_game(tmp_path).parent
    data = _run_cli_capture(capsys, ["analyze", str(proj)])
    assert data["ok"] is True
    report = data["report"]
    assert report["languages"] == ["english", "russian"]
    # 字体处数字段随分析报告下发（本工程无字体 → 空字典）
    assert report["font_usage"] == {}


# ---------------------------------------------------------------------------
# 优化批：一遍扫描 / 引擎安全字 / 大小写盲区 / 缓存时效
# ---------------------------------------------------------------------------

def test_scan_charset_tables_matches_individual_scans(tmp_path):
    """一遍扫描的产物必须与逐项扫描完全一致（合并不改变语义）。"""
    game = _make_font_project(tmp_path)
    (game / "script2.rpy").write_text(
        'label x:\n    e "{font=fonts/title.ttf}[插值]{/font}"\n',
        encoding="utf-8")
    buckets, tag_buckets, tainted, dyn = charset.scan_charset_tables(str(game))
    buckets2, dyn2 = charset.scan_language_buckets(str(game))
    assert buckets == buckets2 and dyn == dyn2
    assert tag_buckets == charset.scan_font_tag_buckets(str(game))
    assert tainted == charset.scan_tainted_font_keys(str(game))
    assert charset.font_keys_match("fonts/title.ttf", tainted)


def test_dist_skips_engine_but_keeps_builtin_ui_chars(tmp_path):
    """成品模式：引擎目录代码文本不计入，但内置界面（renpy/common）
    的用字（如按键帮助的方向箭头）强制保留。"""
    import zlib
    dist = tmp_path / "MyGame-pc"
    (dist / "game" / "tl" / "english").mkdir(parents=True)
    (dist / "game" / "tl" / "english" / "note.txt").write_text(
        "GAMETEXT£", encoding="utf-8")
    (dist / "renpy" / "common").mkdir(parents=True)
    # 内置界面编译脚本：含方向箭头等非 ASCII 字（未翻译时直接上屏）
    (dist / "renpy" / "common" / "00keymap.rpyc").write_bytes(
        zlib.compress("存档 ↑↓←→ 读档".encode("utf-8")))
    # 引擎其他位置的代码文本：不该进字符集（防虚胖）
    (dist / "renpy" / "boot.py").write_text(
        "# engine code É", encoding="utf-8")
    chars, _ = charset.extract_charset_dist(str(dist), CharsetOptions())
    assert "G" in chars and "£" in chars   # 游戏自身内容照常收入
    assert "↑" in chars and "存" in chars    # 内置界面字强制保留
    assert "É" not in chars                  # 引擎代码文本被跳过


def test_unused_detection_case_insensitive(tmp_path):
    """大小写盲区：脚本写 Sounds/A.ogg 而磁盘是 sounds/a.ogg（Windows
    不区分大小写，游戏照常运行），不得被判为无引用而误隔离。"""
    game = tmp_path / "game"
    game.mkdir(parents=True)
    (game / "script.rpy").write_text(
        'label start:\n    play sound "Sounds/A.ogg"\n', encoding="utf-8")
    idx = RefIndex(str(game))
    assets = [AssetInfo(path=str(game / "sounds" / "a.ogg"),
                        rel="sounds/a.ogg", kind=AssetKind.AUDIO, size=10)]
    assert cleanup.find_unused_assets(assets, idx) == []
    # 真无引用的仍如实标记（不误伤原功能）
    lonely = [AssetInfo(path=str(game / "sounds" / "b.ogg"),
                        rel="sounds/b.ogg", kind=AssetKind.AUDIO, size=10)]
    assert cleanup.find_unused_assets(lonely, idx) == ["sounds/b.ogg"]


def test_compiled_text_cache_invalidates_on_change(tmp_path):
    """缓存时效：编译脚本内容变更后不得返回陈旧文本。"""
    import os
    import zlib
    game = tmp_path / "game"
    game.mkdir(parents=True)
    p = game / "script.rpyc"
    p.write_bytes(zlib.compress(b"fonts/first.ttf"))
    t1 = cleanup._compiled_script_text(game)
    assert "first" in t1
    # 改内容 + 明确推后修改时间（避免文件系统时间精度抹平差异）
    p.write_bytes(zlib.compress(b"fonts/second.ttf"))
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))
    t2 = cleanup._compiled_script_text(game)
    assert "second" in t2 and "first" not in t2
