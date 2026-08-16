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
