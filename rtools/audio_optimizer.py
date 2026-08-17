"""音频优化：通过 FFmpeg 转 OGG / 重编码。"""
from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path
from typing import Optional

from .procutil import run_quiet


def find_ffmpeg() -> Optional[str]:
    """按顺序找 ffmpeg：PATH -> 程序旁 bin 目录 -> 源码目录 bin。"""
    found = shutil.which("ffmpeg")
    if found:
        return found
    candidates = []
    if getattr(sys, "frozen", False):       # PyInstaller 打包后
        candidates.append(Path(sys.executable).parent / "bin" / "ffmpeg.exe")
    candidates.append(Path(__file__).resolve().parent.parent / "bin" / "ffmpeg.exe")
    candidates.append(Path(__file__).resolve().parent.parent / "bin" / "ffmpeg")
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def convert_audio(src: str, dst: str, bitrate_k: int) -> Optional[dict]:
    """转码音频（如 WAV/MP3 -> OGG）。结果没变小则不动原文件，返回 None。"""
    return _transcode(src, dst, bitrate_k, ext=".ogg")


def reencode_audio(src: str, dst: str, bitrate_k: int) -> Optional[dict]:
    """同格式重编码（模式 B 用：保持扩展名不变，只压体积）。"""
    ext = Path(src).suffix.lower()
    return _transcode(src, dst, bitrate_k, ext=ext)


def _transcode(src: str, dst: str, bitrate_k: int, ext: str) -> Optional[dict]:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("找不到 FFmpeg，无法处理音频。请安装 FFmpeg 或放入 bin 目录。")

    src_p, dst_p = Path(src), Path(dst)
    old_size = src_p.stat().st_size
    # 审核修复（高-3）：tmp 名带随机后缀——a.wav→a.ogg 的转换 job 与
    # a.ogg 原地重编码 job 曾共用同一 tmp 名，并行下互踩
    tmp = dst_p.with_name(f"{dst_p.name}.rtools.{uuid.uuid4().hex[:8]}.tmp{ext}")

    codec_args = ["-c:a", "libvorbis", "-b:a", f"{bitrate_k}k"] if ext == ".ogg" \
        else ["-b:a", f"{bitrate_k}k"]

    # 单线程是有意为之：libvorbis/libmp3lame 编码器天生不支持多线程，
    # 音频提速靠 _run_jobs 的多 worker 并行（最多 16 路），不靠单任务多线程
    cmd = [ffmpeg, "-y", "-v", "error", "-threads", "1",
           "-i", str(src_p), *codec_args, str(tmp)]
    try:
        proc = run_quiet(cmd, capture_output=True, timeout=600)
        if proc.returncode != 0 or not tmp.exists():
            tmp.unlink(missing_ok=True)
            return None
    except Exception:
        tmp.unlink(missing_ok=True)
        return None

    new_size = tmp.stat().st_size
    if new_size >= old_size:
        tmp.unlink(missing_ok=True)
        return None

    dst_p.parent.mkdir(parents=True, exist_ok=True)
    tmp.replace(dst_p)
    return {"old_size": old_size, "new_size": new_size}
