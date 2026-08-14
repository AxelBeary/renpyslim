"""视频压缩（BACKLOG B7，实验性，默认关闭）。

只做同名同格式重编码（mp4 仍 mp4、webm 仍 webm），不改文件名、
不换容器——工程与成品两种模式都安全。音频流原样保留。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .audio_optimizer import find_ffmpeg
from .procutil import run_quiet

# 档位 → CRF（越大越省、画质越低）
CRF_BY_PRESET = {"conservative": 23, "balanced": 28, "aggressive": 32}
VP9_CRF_BY_PRESET = {"conservative": 30, "balanced": 34, "aggressive": 38}


def compress_video(src: str, dst: str, preset: str = "balanced") -> Optional[dict]:
    """重编码视频到 dst（可与 src 相同，原地）。没变小则不动目标，返回 None。"""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("找不到 FFmpeg，无法处理视频。")

    src_p, dst_p = Path(src), Path(dst)
    ext = src_p.suffix.lower()
    if ext == ".mp4":
        v_codec = ["-c:v", "libx264", "-crf", str(CRF_BY_PRESET.get(preset, 28)),
                   "-preset", "medium", "-pix_fmt", "yuv420p"]
    elif ext in (".webm", ".ogv"):
        v_codec = ["-c:v", "libvpx-vp9", "-crf", str(VP9_CRF_BY_PRESET.get(preset, 34)),
                   "-b:v", "0"]
    else:
        return None

    old_size = src_p.stat().st_size
    tmp = dst_p.with_name(dst_p.name + ".rtools.tmp" + ext)
    cmd = [ffmpeg, "-y", "-v", "error", "-threads", "1",
           "-i", str(src_p), *v_codec, "-c:a", "copy", str(tmp)]
    try:
        proc = run_quiet(cmd, capture_output=True, timeout=7200)
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
