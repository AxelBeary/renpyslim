"""视频压缩（BACKLOG B7，实验性，默认关闭）。

只做同名同格式重编码（mp4 仍 mp4、webm 仍 webm），不改文件名、
不换容器——工程与成品两种模式都安全。音频流原样保留。

编码选择依据 Ren'Py 官方文档（renpy.org/doc/html/movie.html，
2026-08-17 研究确认）——引擎内置 FFmpeg 支持的视频编码为：
AV1 / VP9 / VP8 / Theora / MPEG-4 part 2(Xvid/DivX) / MPEG-2 / MPEG-1；
官方**明确不支持 H.264 解码（和 AAC）**，H.264+MP4 组合仅在 Web
平台靠浏览器解码侥幸能放。因此本模块的安全原则是"同编码重编"：
先用 ffprobe 探测原视频编码，只有原编码在官方支持清单内才重编，
且 mp4 分支仅当原本就是 H.264 时才按 H.264 重编（不新增风险），
其余情况（如 HEVC/Xvid 的 mp4）一律不动并给出警告。
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Optional

from .audio_optimizer import find_ffmpeg
from .config import DEFAULT_PRESET
from .image_optimizer import OptimizeResult
from .procutil import run_quiet

# 档位 → CRF（越大越省、画质越低）
CRF_BY_PRESET = {"conservative": 23, "balanced": 28, "aggressive": 32}
VP9_CRF_BY_PRESET = {"conservative": 30, "balanced": 34, "aggressive": 38}
# 审核修复（中-20）：.ogv 走 Ogg 容器，ffmpeg 的 ogg muxer 没有
# vp9 codec tag，必须用 theora（-q:v 越大画质越好，0~10）
THEORA_Q_BY_PRESET = {"conservative": 8, "balanced": 6, "aggressive": 4}
# AV1（SVT-AV1）：官方支持且官方推荐，同 CRF 比 VP9 再省 30% 左右；
# 但仅 Ren'Py 8.0+ 构建的游戏能放，故作为实验选项（用户拍板可自选）
AV1_CRF_BY_PRESET = {"conservative": 30, "balanced": 34, "aggressive": 38}
# 用户拍板放开多核：视频任务并发少而单个文件大，每个给一半核心
# （旧版 -threads 1，几百 MB webm 单线程编码必超时）
_V_THREADS = max(2, min(16, (os.cpu_count() or 4) // 2))

# Ren'Py 官方支持清单内的视频编码（webm/mkv 容器常见取值）
_WEBM_SAFE_CODECS = {"vp9", "vp8", "av1"}


def probe_video_codec(path: str) -> Optional[str]:
    """用 ffprobe 探测视频流的编码名（如 h264/vp9/hevc），失败返回 None。"""
    from .scanner import find_ffprobe
    ffprobe = find_ffprobe()
    if not ffprobe:
        return None
    try:
        proc = run_quiet(
            [ffprobe, "-v", "quiet", "-print_format", "json",
             "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", path],
            capture_output=True, timeout=30)
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
        streams = data.get("streams") or []
        return streams[0].get("codec_name") if streams else None
    except Exception:
        return None


_encoder_cache: dict = {}


def encoder_available(name: str) -> bool:
    """查本机 ffmpeg 是否带某个编码器（结果缓存）。"""
    if name in _encoder_cache:
        return _encoder_cache[name]
    ffmpeg = find_ffmpeg()
    ok = False
    if ffmpeg:
        try:
            proc = run_quiet([ffmpeg, "-hide_banner", "-encoders"],
                             capture_output=True, timeout=30)
            ok = name in proc.stdout.decode("utf-8", "replace")
        except Exception:
            ok = False
    _encoder_cache[name] = ok
    return ok


def compress_video(src: str, dst: str, preset: str = DEFAULT_PRESET,
                   use_av1: bool = False) -> OptimizeResult:
    """重编码视频到 dst（可与 src 相同，原地）。三态返回（见 OptimizeResult）。

    安全原则（依据官方文档，见模块 docstring）：先探测原编码，
    不在 Ren'Py 官方支持清单内的不动（归 skipped 并带原因，由流水线
    转成用户可见的警告）；在清单内的按同族编码重编；压完没变小也归
    skipped；FFmpeg 执行出错等真错误归 failed。
    use_av1=True（实验选项）时 .webm 用 SVT-AV1 替代 VP9——AV1 更省
    且官方推荐，但仅 Ren'Py 8.0+ 引擎能放，界面侧已带警告。
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("找不到 FFmpeg，无法处理视频。")

    src_p, dst_p = Path(src), Path(dst)
    ext = src_p.suffix.lower()
    codec = probe_video_codec(str(src_p))

    def _refuse(why: str) -> OptimizeResult:
        # 保守拒绝：不盲编、不动目标文件，只留原因（第二波修复：统一归 skipped，
        # 不再抛 RuntimeError，流水线按“格式不适合”记账并转成用户可见警告）
        return OptimizeResult(status="skipped",
                              reason=f"{why}，转码有放不出来的风险，已保留原文件。")

    if ext == ".mp4":
        # 官方不支持 H.264 解码：仅当原文件本来就是 H.264（说明该
        # 游戏接受 H.264）才按 H.264 重编，不新增风险；其他编码
        # （mpeg4/hevc 等）转 H.264 可能让原本能放的变成放不出；
        # 探测返回 None（编码未知）同样保守拒绝，不猜（第二波修复）。
        if codec != "h264":
            return _refuse(
                f"原编码为 {codec or '未知'}：Ren'Py 官方仅支持特定编码")
        v_codec = ["-c:v", "libx264", "-crf", str(CRF_BY_PRESET.get(preset, 28)),
                   "-preset", "medium", "-pix_fmt", "yuv420p"]
    elif ext == ".webm":
        # 第二波修复：探测返回 None（编码未知）不再盲编，与 mp4 分支一致的
        # 保守拒绝——保持“先探测、不在清单内不动”的模块原则。
        if not codec or codec not in _WEBM_SAFE_CODECS:
            return _refuse(
                f"原编码为 {codec or '未知'}：不在 Ren'Py 官方支持清单内")
        # 原编码就是 AV1：维持 AV1 重编（游戏本来就放 AV1，零兼容风险，
        # 且 SVT-AV1 实测更快更省）——不需要用户勾选；只有把非 AV1
        # 转成 AV1 才算实验行为（需 use_av1，界面带 8.0+ 警告）
        want_av1 = codec == "av1" or use_av1
        if want_av1 and encoder_available("libsvtav1"):
            # AV1：官方支持且推荐，同 CRF 比 VP9 更省
            v_codec = ["-c:v", "libsvtav1",
                       "-crf", str(AV1_CRF_BY_PRESET.get(preset, 34)),
                       "-preset", "6"]
        else:
            # -row-mt 1：VP9 行级多线程，编码快数倍，画质/体积不变
            v_codec = ["-c:v", "libvpx-vp9",
                       "-crf", str(VP9_CRF_BY_PRESET.get(preset, 34)),
                       "-b:v", "0", "-row-mt", "1"]
    elif ext == ".ogv":
        # 第二波修复：同上，探测返回 None（编码未知）也保守拒绝，不盲编。
        if codec != "theora":
            return _refuse(
                f"原编码为 {codec or '未知'}：ogv 容器按官方支持清单应为 Theora")
        v_codec = ["-c:v", "libtheora", "-q:v",
                   str(THEORA_Q_BY_PRESET.get(preset, 6))]
    else:
        return OptimizeResult(status="skipped",
                              reason=f"不支持的视频容器格式 {ext}")

    # 审核修复（高-3）：tmp 名带随机后缀防并行踩踏；old_size 挪进 try：
    # 源文件不存在/被占用等真错误归 failed，不再裸抛（第二波修复）
    tmp = dst_p.with_name(f"{dst_p.name}.rtools.{uuid.uuid4().hex[:8]}.tmp{ext}")
    try:
        old_size = src_p.stat().st_size
        cmd = [ffmpeg, "-y", "-v", "error", "-threads", str(_V_THREADS),
               "-i", str(src_p), *v_codec, "-c:a", "copy", str(tmp)]
        proc = run_quiet(cmd, capture_output=True, timeout=7200)
        if proc.returncode != 0 or not tmp.exists():
            tmp.unlink(missing_ok=True)
            err = proc.stderr.decode("utf-8", "replace").strip() if proc.stderr else ""
            return OptimizeResult(
                status="failed",
                reason=f"FFmpeg 重编码失败（退出码 {proc.returncode}）{err[:200]}")
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return OptimizeResult(status="failed", reason=str(e))

    new_size = tmp.stat().st_size
    if new_size >= old_size:
        tmp.unlink(missing_ok=True)
        return OptimizeResult(status="skipped",
                              reason="已是最优，压不出更小")

    dst_p.parent.mkdir(parents=True, exist_ok=True)
    tmp.replace(dst_p)
    return OptimizeResult(status="ok", path=str(dst_p),
                          old_size=old_size, new_size=new_size)
