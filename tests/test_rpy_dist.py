"""带 .rpy 源码的成品：成品模式自动解锁格式转换的回归测试。"""
from __future__ import annotations

import wave
from pathlib import Path

import pytest                                       # noqa: E402

from rtools import pipeline                          # noqa: E402
from rtools.audio_optimizer import find_ffmpeg       # noqa: E402
from rtools.config import default_options            # noqa: E402


def _make_dist_with_rpy(tmp_path: Path) -> Path:
    """造一个罕见的"带源码发布"成品。"""
    dist = tmp_path / "MyGame-pc"
    game = dist / "game"
    (game / "audio").mkdir(parents=True)
    (game / "images").mkdir(parents=True)

    (game / "script.rpy").write_text(
        'play sound "audio/s.wav"\n'
        'scene "images/bg.png"\n', encoding="utf-8")
    (game / "script.rpyc").write_bytes(b"RENPY RPC2 dummy")
    (dist / "MyGame.exe").write_bytes(b"MZ")

    # 足够大的 WAV（噪声），保证转 OGG 后确实变小
    wav_path = game / "audio" / "s.wav"
    import os
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(os.urandom(44100 * 2 * 2))   # 2 秒

    # 足够大的 PNG（随机像素难以压缩），保证转 WebP 后确实变小
    from PIL import Image
    import os
    im = Image.new("RGB", (320, 320))
    im.frombytes(os.urandom(320 * 320 * 3))
    im.save(game / "images" / "bg.png")
    return dist


@pytest.mark.skipif(not find_ffmpeg(),
                    reason="本机无 ffmpeg，无法验证 WAV→OGG 转换")
def test_dist_with_rpy_unlocks_conversion(tmp_path):
    dist = _make_dist_with_rpy(tmp_path)
    opts = default_options()

    result = pipeline.run_dist(
        str(dist), opts,
        work_root=str(tmp_path / "work"),
        output_dir=str(tmp_path / "out"))

    assert result["has_rpy"] is True
    wd = Path(result["working_dir"])

    # WAV → OGG：旧文件删除、新文件就位
    assert not (wd / "game" / "audio" / "s.wav").exists()
    assert (wd / "game" / "audio" / "s.ogg").exists()
    # PNG → WebP
    assert not (wd / "game" / "images" / "bg.png").exists()
    assert (wd / "game" / "images" / "bg.webp").exists()

    # 脚本引用已同步改写
    script = (wd / "game" / "script.rpy").read_text(encoding="utf-8")
    assert "audio/s.ogg" in script
    assert "images/bg.webp" in script
    assert "s.wav" not in script and "bg.png" not in script

    # 原件纹丝不动
    assert (dist / "game" / "audio" / "s.wav").exists()
    assert (dist / "game" / "images" / "bg.png").exists()


def _image_only_options():
    opts = default_options()
    opts.do_audio = False
    opts.do_fonts = False
    opts.use_cache = False
    return opts


def test_dist_ambiguous_bare_name_keeps_nested_png(tmp_path, monkeypatch):
    """只写 bg.png 时，根目录资产可转换，歧义嵌套资产必须留源留名。"""
    from PIL import Image

    monkeypatch.setenv("RENPYSLIM_HOME", str(tmp_path / "home"))
    dist = tmp_path / "Game"
    game = dist / "game"
    (game / "images").mkdir(parents=True)
    Image.new("RGB", (800, 600), (220, 20, 20)).save(
        game / "bg.png", "PNG", optimize=True)
    Image.new("RGB", (800, 600), (20, 20, 220)).save(
        game / "images" / "bg.png", "PNG", optimize=True)
    (game / "script.rpy").write_text(
        'image hero = "bg.png"\n', encoding="utf-8")

    result = pipeline.run_dist(
        str(dist), _image_only_options(),
        work_root=str(tmp_path / "work"), output_dir=str(tmp_path / "out"))
    wd = Path(result["working_dir"]) / "game"

    assert not (wd / "bg.png").exists()
    assert (wd / "bg.webp").exists()
    assert (wd / "images" / "bg.png").exists(), "歧义裸名放行了嵌套资产改名"
    assert not (wd / "images" / "bg.webp").exists()
    assert (wd / "script.rpy").read_text(encoding="utf-8") == (
        'image hero = "bg.webp"\n')
    # 原件始终不动；转换只发生在工作副本。
    assert (game / "bg.png").exists()
    assert (game / "images" / "bg.png").exists()


def test_dist_unique_nested_bare_name_converts(tmp_path, monkeypatch):
    """唯一嵌套资产的合法裸名引用仍应解锁转换。"""
    from PIL import Image

    monkeypatch.setenv("RENPYSLIM_HOME", str(tmp_path / "home"))
    dist = tmp_path / "Game"
    game = dist / "game"
    (game / "images").mkdir(parents=True)
    Image.new("RGB", (800, 600), (20, 220, 20)).save(
        game / "images" / "bg.png", "PNG", optimize=True)
    (game / "script.rpy").write_text('scene "bg.png"\n', encoding="utf-8")

    result = pipeline.run_dist(
        str(dist), _image_only_options(),
        work_root=str(tmp_path / "work"), output_dir=str(tmp_path / "out"))
    wd = Path(result["working_dir"]) / "game"

    assert not (wd / "images" / "bg.png").exists()
    assert (wd / "images" / "bg.webp").exists()
    assert (wd / "script.rpy").read_text(encoding="utf-8") == (
        'scene "bg.webp"\n')


def test_dist_without_rpy_stays_safename(tmp_path):
    """没有源码的普通成品：维持同名策略，绝不换格式。"""
    dist = _make_dist_with_rpy(tmp_path)
    (dist / "game" / "script.rpy").unlink()     # 去掉源码

    result = pipeline.run_dist(
        str(dist), default_options(),
        work_root=str(tmp_path / "work2"),
        output_dir=str(tmp_path / "out2"))

    assert result["has_rpy"] is False
    wd = Path(result["working_dir"])
    assert (wd / "game" / "audio" / "s.wav").exists(), "无源码不许换格式"
    assert not (wd / "game" / "audio" / "s.ogg").exists()
    assert (wd / "game" / "images" / "bg.png").exists()
