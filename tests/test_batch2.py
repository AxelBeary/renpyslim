"""第三批（借鉴清单 B1~B9）功能的回归测试。"""
from __future__ import annotations

import textwrap
import zlib
from pathlib import Path

import pytest

from rtools import cache, rpa
from rtools.image_optimizer import quantize_png
from rtools.remap import build_remap_script
from rtools.video_optimizer import compress_video
from rtools.audio_optimizer import find_ffmpeg


# ---------------------------------------------------------------------------
# B1：RPA 多密钥头部兼容
# ---------------------------------------------------------------------------

def test_rpa_multi_key_header(tmp_path):
    """头部写两个密钥字段，读取端应全部异或成真实密钥。"""
    import pickle
    keys = (0x11111111, 0x03254769)
    key_eff = keys[0] ^ keys[1]          # = 0x12345678
    body = b"hello multi key"
    header_len = 43                       # 双密钥头部长度
    offset = header_len + len(body)
    entries = {"x.txt": [(header_len ^ key_eff, len(body) ^ key_eff, b"")]}
    index = zlib.compress(pickle.dumps(entries, 2))
    header = f"RPA-3.0 {offset:016x} {keys[0]:08x} {keys[1]:08x}\n".encode()
    assert len(header) == header_len
    p = tmp_path / "multi_key.rpa"
    p.write_bytes(header + body + index)

    arc = rpa.RpaArchive(str(p))
    assert arc.key == key_eff
    assert arc.read("x.txt") == body
    arc.close()


# ---------------------------------------------------------------------------
# B2：重建封包沿用原密钥
# ---------------------------------------------------------------------------

def test_rebuild_keeps_source_key(tmp_path):
    src = tmp_path / "src.rpa"
    w = rpa.RpaWriter(str(src), key=0xABCDEF01)
    w.add("f.png", b"PNGDATA" * 50)
    w.close()

    dst = tmp_path / "dst.rpa"
    rpa.rebuild_archive(str(src), str(dst), {})
    arc = rpa.RpaArchive(str(dst))
    assert arc.key == 0xABCDEF01, "重建必须沿用源封包密钥"
    assert arc.read("f.png") == b"PNGDATA" * 50
    arc.close()


# ---------------------------------------------------------------------------
# B5：PNG 有损量化
# ---------------------------------------------------------------------------

def _big_truecolor_png(path: Path):
    import os
    from PIL import Image
    im = Image.new("RGB", (300, 300))
    im.frombytes(os.urandom(300 * 300 * 3))
    im.save(path, "PNG")


def test_quantize_png_shrinks_truecolor(tmp_path):
    p = tmp_path / "big.png"
    _big_truecolor_png(p)
    before = p.stat().st_size
    res = quantize_png(str(p), str(p))
    assert res is not None
    assert res["new_size"] < before * 0.5, "真彩随机图量化应省一半以上"
    assert p.stat().st_size == res["new_size"]


def test_quantize_skips_palette_png(tmp_path):
    from PIL import Image
    p = tmp_path / "pal.png"
    Image.new("P", (50, 50)).save(p)
    assert quantize_png(str(p), str(p)) is None, "调色板图不该量化"


# ---------------------------------------------------------------------------
# B9：运行时重映射脚本
# ---------------------------------------------------------------------------

def test_remap_script_valid_python():
    script = build_remap_script({"images/bg.png": "images/bg.webp",
                                 "gui\\logo.jpg": "gui/logo.webp"})
    assert "_renpyslim_remap" in script
    assert "images/bg.png" in script
    assert "gui/logo.webp" in script
    # 提取 python 块做语法编译检查（不执行）
    body = script.split("init -999 python:", 1)[1]
    body = textwrap.dedent(body)
    compile(body, "rtools_remap", "exec")


# ---------------------------------------------------------------------------
# B6：增量缓存
# ---------------------------------------------------------------------------

def test_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    src = tmp_path / "s.png"
    src.write_bytes(b"x" * 5000)
    optimized = tmp_path / "o.png"
    optimized.write_bytes(b"y" * 1000)

    assert cache.lookup(str(src), "img|test") is None
    cache.store(str(src), "img|test", str(optimized))
    hit = cache.lookup(str(src), "img|test")
    assert hit is not None

    dst = tmp_path / "applied.png"
    assert cache.apply_cached(hit, str(dst))
    assert dst.read_bytes() == b"y" * 1000
    # 动作键不同则不命中
    assert cache.lookup(str(src), "img|other") is None


# ---------------------------------------------------------------------------
# B7：视频压缩（需要 ffmpeg）
# ---------------------------------------------------------------------------

def test_video_compress(tmp_path):
    from rtools.procutil import run_quiet
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        pytest.skip("本机无 ffmpeg")
    src = tmp_path / "v.mp4"
    run_quiet([ffmpeg, "-y", "-v", "error", "-f", "lavfi",
               "-i", "testsrc=duration=2:size=320x240:rate=24",
               "-c:v", "libx264", "-crf", "18", str(src)],
              capture_output=True, timeout=120)
    assert src.exists() and src.stat().st_size > 0
    before = src.stat().st_size
    res = compress_video(str(src), str(src), "balanced")
    assert res is not None
    assert res["new_size"] < before
