"""核心回归测试：守住已修复的 bug 不再复发。

覆盖：RPA 封包读写、引用改写安全性、字体/图片优化不损坏原文件、
rpyc 解析、配置默认值。
"""
from __future__ import annotations

import json
import pickle
import random
import struct
import zlib
from pathlib import Path

import pytest

from rtools import pipeline, rpa                          # noqa: E402
from rtools.charset import read_rpyc_text                # noqa: E402
from rtools.config import OptimizeOptions, default_options  # noqa: E402
from rtools.font_optimizer import subset_font            # noqa: E402
from rtools.image_optimizer import optimize_image        # noqa: E402
from rtools.refs import RefIndex                         # noqa: E402


# ---------------------------------------------------------------------------
# RPA 封包
# ---------------------------------------------------------------------------

def test_rpa_roundtrip(tmp_path):
    arc_path = tmp_path / "archive.rpa"
    w = rpa.RpaWriter(str(arc_path), key=0x12345678)
    w.add("images/bg.png", b"PNGDATA" * 100)
    w.add("audio/bgm.ogg", b"OGGDATA" * 50)
    w.add("deep/nested/file.txt", b"hello")
    w.close()

    arc = rpa.RpaArchive(str(arc_path))
    assert arc.version == "RPA-3.0"
    assert arc.names() == ["audio/bgm.ogg", "deep/nested/file.txt", "images/bg.png"]
    assert arc.read("images/bg.png") == b"PNGDATA" * 100
    assert arc.read("deep/nested/file.txt") == b"hello"
    arc.close()


def test_rpa_rebuild_with_replacement(tmp_path):
    src = tmp_path / "a.rpa"
    w = rpa.RpaWriter(str(src))
    w.add("x.png", b"OLD" * 100)
    w.add("y.png", b"KEEP" * 100)
    w.close()

    new_file = tmp_path / "optimized.png"
    new_file.write_bytes(b"NEW" * 30)
    dest = tmp_path / "b.rpa"
    replaced, total = rpa.rebuild_archive(str(src), str(dest), {"x.png": str(new_file)})
    assert (replaced, total) == (1, 2)

    arc = rpa.RpaArchive(str(dest))
    assert arc.read("x.png") == b"NEW" * 30
    assert arc.read("y.png") == b"KEEP" * 100
    arc.close()


def _rpa_rebuild_fixture(path: Path, image_data: bytes,
                         filler_count: int = 500) -> None:
    """写合法 RPA-3.0：默认 zlib 索引 + 大量共享长前缀条目。

    源索引使用 zlib 默认级别，重建器使用 level 3；条目足够多时，
    一个字节的资产收益也会被索引/格式开销吃掉。
    """
    key = 0x42424242
    placeholder = b"RPA-3.0 " + b"X" * 16 + b" " + b"X" * 8 + b"\n"
    body = bytearray(placeholder)
    entries = {}
    for i in range(filler_count):
        name = f"data/very-long-shared-prefix/chapter-{i:05d}-asset.dat"
        data = f"payload-{i}".encode() * 4
        body += b"Made with Ren'Py."
        offset = len(body)
        body += data
        entries[name] = [(offset ^ key, len(data) ^ key, b"")]
    body += b"Made with Ren'Py."
    offset = len(body)
    body += image_data
    entries["images/bg.png"] = [(offset ^ key, len(image_data) ^ key, b"")]
    index_offset = len(body)
    index = zlib.compress(pickle.dumps(entries, 2))
    path.write_bytes(
        b"RPA-3.0 %016x %08x\n" % (index_offset, key)
        + body[len(placeholder):] + index)


def _png_with_redundant_byte(path: Path) -> bytes:
    """生成已优化 PNG 并附加一个冗余字节；同名优化确定只省这一字节。"""
    from PIL import Image

    pixels = random.Random(42).randbytes(64 * 64 * 3)
    Image.frombytes("RGB", (64, 64), pixels).save(path, "PNG", optimize=True)
    return path.read_bytes() + b"\0"


def _image_only_options() -> OptimizeOptions:
    opts = OptimizeOptions()
    opts.use_cache = False
    opts.do_audio = False
    opts.do_fonts = False
    return opts


def test_dist_rpa_keeps_original_when_rebuild_grows(tmp_path):
    """资产只省 1 字节但封包反涨时：原包不动、临时包删、saved 不虚报。"""
    dist = tmp_path / "Game-pc"
    game = dist / "game"
    game.mkdir(parents=True)
    image_data = _png_with_redundant_byte(tmp_path / "base.png")
    src = game / "archive.rpa"
    _rpa_rebuild_fixture(src, image_data)
    before = src.read_bytes()

    result = pipeline.run_dist(
        str(dist), _image_only_options(), str(tmp_path / "work"),
        str(tmp_path / "out"))

    rebuilt = Path(result["working_dir"]) / "game" / "archive.rpa"
    assert src.read_bytes() == before
    assert rebuilt.read_bytes() == before
    assert not list(Path(result["working_dir"]).rglob("*.rtools.tmp"))
    assert result["saved_bytes"] <= 0
    assert any("archive.rpa" in w and "未变小" in w
               for w in result["warnings"])
    changelog = json.loads(
        (tmp_path / "out" / "changelog.json").read_text(encoding="utf-8"))
    assert changelog["saved_bytes"] == result["saved_bytes"]
    assert not any(r["action"] == "rpa_rebuild"
                   for r in changelog["records"])


def test_dist_rpa_equal_size_keeps_original(tmp_path, monkeypatch):
    """new_size == old_size 也必须走不替换分支（>= 边界）。"""
    from shutil import copyfile

    dist = tmp_path / "Game-pc"
    game = dist / "game"
    game.mkdir(parents=True)
    src = game / "archive.rpa"
    _rpa_rebuild_fixture(
        src, _png_with_redundant_byte(tmp_path / "base.png"))
    before = src.read_bytes()

    def copy_unchanged(source, dest, replacements):
        copyfile(source, dest)
        return len(replacements), 501

    monkeypatch.setattr(pipeline.rpa, "rebuild_archive", copy_unchanged)
    result = pipeline.run_dist(
        str(dist), _image_only_options(), str(tmp_path / "work"),
        str(tmp_path / "out"))

    rebuilt = Path(result["working_dir"]) / "game" / "archive.rpa"
    assert rebuilt.read_bytes() == before
    assert not list(Path(result["working_dir"]).rglob("*.rtools.tmp"))
    assert result["saved_bytes"] <= 0
    assert any("archive.rpa" in w and "未变小" in w
               for w in result["warnings"])


def test_dist_rpa_growth_keeps_renamed_loose_fallback(tmp_path):
    """改名转换遇封包反涨：原包保留，WebP 散落副本与改写引用仍是兜底。"""
    from PIL import Image

    dist = tmp_path / "Game-pc"
    game = dist / "game"
    game.mkdir(parents=True)
    # 渐变图转 WebP 收益很小，会被 20000 条目的索引开销反超，从而触发
    # "封包未变小"分支；体积又大于 1KB，不会被体积门槛跳过。
    #
    # optimize 必须为 False：pipeline 用的是 conservative 档的 quality=95，
    # 已 optimize=True 的 PNG（3353B）在 q=95 下转出的 WebP 反而更大，
    # optimize_image 按"没变小就不替换"返回 skipped（"已是最优，压不出更小"），
    # 于是根本不生成 images/bg.webp，本用例要验的改名兜底路径也就无从触发。
    # optimize=False 的同类图（3861B）转出 3550B，转换成功，才走得到封包反涨。
    Image.linear_gradient("L").resize((800, 800)).convert("RGB").save(
        tmp_path / "base.png", "PNG", optimize=False)
    image_data = (tmp_path / "base.png").read_bytes()
    src = game / "archive.rpa"
    _rpa_rebuild_fixture(src, image_data, filler_count=20_000)
    # 转换产物要写到 game/images/bg.webp，转换前目标目录必须存在
    (game / "images").mkdir()
    script = game / "script.rpy"
    script.write_text('image bg = "images/bg.png"\n', encoding="utf-8")
    before = src.read_bytes()

    result = pipeline.run_dist(
        str(dist), _image_only_options(), str(tmp_path / "work"),
        str(tmp_path / "out"))

    working_game = Path(result["working_dir"]) / "game"
    rebuilt = working_game / "archive.rpa"
    loose = working_game / "images" / "bg.webp"
    assert src.read_bytes() == before
    assert rebuilt.read_bytes() == before
    assert loose.is_file()
    with Image.open(loose) as im:
        im.verify()
    assert 'images/bg.webp' in (working_game / "script.rpy").read_text(
        encoding="utf-8")
    arc = rpa.RpaArchive(str(rebuilt))
    try:
        assert "images/bg.png" in arc.names()
        assert "images/bg.webp" not in arc.names()
        assert arc.read("images/bg.png") == image_data
    finally:
        arc.close()
    assert not list(Path(result["working_dir"]).rglob("*.rtools.tmp"))
    assert result["saved_bytes"] <= 0
    assert any("archive.rpa" in w and "未变小" in w
               for w in result["warnings"])
    changelog = json.loads(
        (tmp_path / "out" / "changelog.json").read_text(encoding="utf-8"))
    assert changelog["saved_bytes"] == result["saved_bytes"]
    assert not any(r["action"] == "rpa_rebuild"
                   for r in changelog["records"])


def test_dist_rpa_rebuild_saved_matches_archive_delta(tmp_path):
    """重建成功时 saved 必须按封包实际净变化记账，而非资产收益之和。"""
    from PIL import Image

    dist = tmp_path / "Game-pc"
    game = dist / "game"
    game.mkdir(parents=True)
    image = tmp_path / "bg.png"
    # 未优化的渐变图：同名压缩能确定省下字节，重建后封包才会变小
    Image.linear_gradient("L").resize((800, 800)).convert("RGB").save(
        image, "PNG", optimize=False)
    src = game / "archive.rpa"
    _rpa_rebuild_fixture(src, image.read_bytes())
    before = src.stat().st_size

    result = pipeline.run_dist(
        str(dist), _image_only_options(), str(tmp_path / "work"),
        str(tmp_path / "out"))

    rebuilt = Path(result["working_dir"]) / "game" / "archive.rpa"
    actual_saved = before - rebuilt.stat().st_size
    assert actual_saved > 0
    assert result["saved_bytes"] == actual_saved
    changelog = json.loads(
        (tmp_path / "out" / "changelog.json").read_text(encoding="utf-8"))
    assert changelog["saved_bytes"] == actual_saved
    assert any(r["action"] == "rpa_rebuild" for r in changelog["records"])


def test_rpa_rejects_non_archive(tmp_path):
    p = tmp_path / "fake.rpa"
    p.write_bytes(b"not an archive at all")
    with pytest.raises(rpa.RpaError):
        rpa.RpaArchive(str(p))


def test_rpa_reads_legacy_7x_style(tmp_path):
    """旧版（7.x）封包：只有偏移异或、带 25 字节前缀，必须能读。"""
    import pickle as _pk
    HEADER_LEN = 34   # len(b"RPA-3.0 " + 16 + b" " + 8 + b"\n")
    data = b"X" * 200
    prefix = data[:25]
    key = 0xDEADBEEF
    body_pad = b"JUNK"                       # 文件体开头可有任意内容
    body = body_pad + prefix + data[len(prefix):]
    # 条目偏移是绝对文件位置：前缀冗余副本在体中，读时跳过
    offset = HEADER_LEN + len(body_pad)
    entries = {"old.png": [(offset ^ key, len(data), prefix)]}
    index = zlib.compress(_pk.dumps(entries, 2))
    p = tmp_path / "legacy.rpa"
    p.write_bytes(b"RPA-3.0 %016x %08x\n" % (HEADER_LEN + len(body), key)
                  + body + index)
    arc = rpa.RpaArchive(str(p))
    assert arc.read("old.png") == data
    arc.close()


def test_rpa_rejects_unsafe_pickle(tmp_path):
    """恶意封包想用 pickle 执行代码：受限反序列化必须拒绝。"""
    import pickle

    class Evil:
        def __reduce__(self):
            return (eval, ("__import__('os').system('echo pwned')",))

    payload = zlib.compress(pickle.dumps({"x": Evil()}, 2))
    p = tmp_path / "evil.rpa"
    p.write_bytes(b"RPA-3.0 " + b"%016x %08x\n" % (0, 0) + payload)
    with pytest.raises(rpa.RpaError):
        rpa.RpaArchive(str(p))


# ---------------------------------------------------------------------------
# 引用改写
# ---------------------------------------------------------------------------

def _make_game(tmp_path, files: dict):
    game = tmp_path / "game"
    for rel, content in files.items():
        p = game / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8", newline="")
    return game


def test_refs_rewrite_basic(tmp_path):
    game = _make_game(tmp_path, {
        "script.rpy": 'scene "images/bg.png"\nplay music "audio/bgm.ogg"\n',
    })
    idx = RefIndex(str(game))
    assert idx.find("images/bg.png")
    recs = idx.rewrite({"images/bg.png": "images/bg.webp"})
    assert recs and recs[0].action == "rename_ref"
    assert 'scene "images/bg.webp"' in (game / "script.rpy").read_text(encoding="utf-8")


def test_refs_bare_name_never_matches_inside_path(tmp_path):
    """裸文件名替换绝不能命中别的路径内部（左侧守卫）。"""
    game = _make_game(tmp_path, {
        "a.rpy": '"gui/menu.png"\n"menu.png"\n',
    })
    idx = RefIndex(str(game))
    idx.rewrite({"menu.png": "menu.webp"})   # 裸名映射
    out = (game / "a.rpy").read_text(encoding="utf-8")
    assert '"gui/menu.png"' in out        # 路径内的不能被裸名误伤
    assert '"menu.webp"' in out           # 真正的裸引用要改掉


def test_refs_non_utf8_roundtrip_lossless(tmp_path):
    """含非 UTF-8 字节的脚本：改写后其余字节必须逐字节无损。"""
    junk = b'\xff\xfe garbage \x9c bytes\nplay sound "s.wav"\n'
    game = _make_game(tmp_path, {"old.rpy": junk})
    idx = RefIndex(str(game))
    idx.rewrite({"s.wav": "s.ogg"})
    out = (game / "old.rpy").read_bytes()
    assert out.startswith(b"\xff\xfe garbage \x9c bytes\n")
    assert b'"s.ogg"' in out


# ---------------------------------------------------------------------------
# 字体瘦身（致命 bug 回归：原地模式不许删原文件）
# ---------------------------------------------------------------------------

def _make_tiny_font(path: Path):
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    fb = FontBuilder(1000, isTTF=True)
    glyphs = [".notdef", "A", "B"]
    fb.setupGlyphOrder(glyphs)
    fb.setupCharacterMap({65: "A", 66: "B"})
    pen_map = {}
    for g in glyphs:
        pen = TTGlyphPen(None)
        pen.moveTo((0, 0)); pen.lineTo((0, 700)); pen.lineTo((500, 700)); pen.closePath()
        pen_map[g] = pen.glyph()
    fb.setupGlyf(pen_map)
    fb.setupHorizontalMetrics({g: (600, 0) for g in glyphs})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "T", "styleName": "R"})
    fb.setupOS2()
    fb.setupPost()
    fb.save(str(path))


def test_font_subset_in_place_never_deletes_original(tmp_path, monkeypatch):
    font = tmp_path / "t.ttf"
    _make_tiny_font(font)
    # 只保留 A：应成功，文件仍在
    res = subset_font(str(font), str(font), {"A", " "})
    assert res["glyphs_after"] < res["glyphs_before"] or res["new_size"] < res["old_size"]
    assert font.exists()

    # 模拟"瘦身结果反而变大"：打桩让临时文件写入垃圾数据，
    # 必须抛异常且原字体逐字节完好，不留临时文件残留
    _make_tiny_font(font)
    before = font.read_bytes()
    import fontTools.ttLib as _tt

    class _FatFont(_tt.TTFont):
        def save(self, path):
            Path(path).write_bytes(b"\x00" * (len(before) + 9999))

    monkeypatch.setattr(
        "rtools.font_optimizer.TTFont",
        lambda *a, **k: _FatFont(str(font), fontNumber=0, lazy=True))
    with pytest.raises(ValueError):
        subset_font(str(font), str(font), {"A"})
    assert font.exists()
    assert font.read_bytes() == before
    assert not list(tmp_path.glob("*.rtools.tmp"))


# ---------------------------------------------------------------------------
# 图片优化
# ---------------------------------------------------------------------------

def test_image_never_grows_in_place(tmp_path):
    from PIL import Image
    p = tmp_path / "t.png"
    # 已经是极致的 1x1 图：优化不应替换，更不能把文件弄坏
    Image.new("RGBA", (1, 1), (255, 0, 0, 255)).save(p, "PNG", optimize=True)
    before = p.read_bytes()
    res = optimize_image(str(p), str(p), quality=85)
    if not res:
        # 三态：已是最优归 skipped（不是失败），文件绝不能被动过
        assert res["status"] in ("skipped", "failed")
        assert p.read_bytes() == before
    else:
        assert res["new_size"] < res["old_size"]


def test_image_webp_conversion(tmp_path):
    from PIL import Image
    p = tmp_path / "big.png"
    Image.new("RGB", (800, 600), (120, 30, 200)).save(p, "PNG")
    dst = tmp_path / "big.webp"
    res = optimize_image(str(p), str(dst), quality=85, convert_webp=True)
    assert res and res["converted"]
    assert dst.exists() and res["new_size"] < res["old_size"]


# ---------------------------------------------------------------------------
# rpyc 解析（新格式槽位表 + 旧格式）
# ---------------------------------------------------------------------------

def test_read_rpyc_new_format(tmp_path):
    inner = "你好 illurock.opus textbox".encode("utf-8")
    slot1 = zlib.compress(inner)
    body = b"RENPY RPC2" + struct.pack("III", 1, 46, len(slot1)) \
        + struct.pack("III", 0, 0, 0) + struct.pack("III", 0, 0, 0) + slot1
    p = tmp_path / "script.rpyc"
    p.write_bytes(body)
    text = read_rpyc_text(p)
    assert "illurock.opus" in text and "你好" in text


def test_read_rpyc_legacy_format(tmp_path):
    p = tmp_path / "old.rpyc"
    p.write_bytes(zlib.compress("legacy 旧格式".encode("utf-8")))
    assert "legacy" in read_rpyc_text(p)


# ---------------------------------------------------------------------------
# 配置默认值（安全红线）
# ---------------------------------------------------------------------------

def test_safe_defaults():
    opts = default_options()
    assert opts.in_place is False                # 默认不动原件
    assert opts.delete_unreferenced is False     # 默认不删文件
    assert opts.preset == "conservative"          # 用户拍板：默认画质优先档
    assert opts.charset.base_latin is True       # 保底字符集默认开
    assert opts.do_videos is False               # 视频默认关
