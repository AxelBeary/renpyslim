"""智能分析器：把扫描结果变成带问题、建议、优先级的报告。"""
from __future__ import annotations

from .models import (
    AnalysisEntry, AnalysisReport, AssetInfo, AssetKind, Issue, Priority,
)
from . import config as cfg
from .utils import fmt_size as _fmt_size


def _kb(size: int) -> float:
    return size / 1024


def _analyze_image(a: AssetInfo, mode: str,
                   apk_remap: bool = False) -> AnalysisEntry:
    entry = AnalysisEntry(asset=a)
    kb = _kb(a.size)
    is_png = a.ext == ".png"

    if mode == "apk":
        # APK 只读预分析：只按条目体积与格式给粗略预估。
        # 默认档只做同名重压；开“最大瘦身”才会 PNG/JPG → WebP。
        ext = a.ext
        if ext in (".png", ".jpg", ".jpeg") and a.size >= 1024:
            if apk_remap:
                entry.priority = Priority.MEDIUM if kb >= 1024 else Priority.LOW
                entry.issues.append(Issue(
                    message=f"可换成更省体积的 WebP（{_fmt_size(a.size)}）。",
                    suggestion="开启“最大瘦身”后转 WebP，通常能再省一半左右。",
                ))
                entry.est_saving = int(a.size * 0.5)
            else:
                entry.priority = Priority.LOW
                entry.issues.append(Issue(
                    message=f"可同名重压（{_fmt_size(a.size)}）。",
                    suggestion="默认档会按当前画质档重压；想再省一半可开启“最大瘦身”转 WebP。",
                ))
                entry.est_saving = int(a.size * (0.15 if is_png else 0.08))
        return entry

    if kb >= cfg.HUGE_IMAGE_KB:
        entry.priority = Priority.HIGH
        entry.issues.append(Issue(
            message=f"图片体积巨大（{_fmt_size(a.size)}），会明显拖慢加载并撑大包体。",
            suggestion="优先压缩；若是背景/立绘类大图，转 WebP 收益最大。",
        ))
        entry.est_saving = int(a.size * 0.6)
    elif kb >= cfg.LARGE_IMAGE_KB:
        entry.priority = Priority.MEDIUM
        entry.issues.append(Issue(
            message=f"图片偏大（{_fmt_size(a.size)}）。",
            suggestion="建议压缩；PNG 转 WebP 通常能省一半以上。",
        ))
        entry.est_saving = int(a.size * (0.6 if is_png else 0.3))

    if a.width and a.height and max(a.width, a.height) > 4096:
        entry.priority = Priority.HIGH if entry.priority == Priority.LOW else entry.priority
        entry.issues.append(Issue(
            message=f"分辨率过高（{a.width}×{a.height}），超过大多数屏幕显示需要。",
            suggestion="如非必须高清，可考虑缩小分辨率（当前版本先提示，需手动处理）。",
        ))

    if is_png and mode == "project" and entry.priority == Priority.LOW and kb > 100:
        entry.issues.append(Issue(
            message="PNG 格式体积普遍偏大。",
            suggestion="可随流程批量转为 WebP（工具会自动改写脚本引用）。",
        ))
        entry.est_saving = entry.est_saving or int(a.size * 0.5)
    return entry


def _analyze_audio(a: AssetInfo, mode: str,
                   apk_remap: bool = False) -> AnalysisEntry:
    entry = AnalysisEntry(asset=a)
    kb = _kb(a.size)

    if mode == "apk":
        # 默认档只重编 OGG/MP3 的码率；WAV 要有重映射（最大瘦身）才转 OGG
        if a.ext == ".wav":
            if apk_remap:
                entry.priority = Priority.HIGH if kb > 512 else Priority.MEDIUM
                entry.issues.append(Issue(
                    message=f"WAV 无压缩，体积大（{_fmt_size(a.size)}）。",
                    suggestion="开启“最大瘦身”后转 OGG，通常能省 80% 左右。",
                ))
                entry.est_saving = int(a.size * 0.8)
            else:
                entry.priority = Priority.LOW
                entry.issues.append(Issue(
                    message=f"WAV 无压缩（{_fmt_size(a.size)}），默认档不会处理。",
                    suggestion="需要开启“最大瘦身”才能转 OGG。",
                ))
        elif a.ext in (".mp3", ".ogg"):
            entry.priority = Priority.LOW
            entry.issues.append(Issue(
                message=f"可降码率重编（{_fmt_size(a.size)}）。",
                suggestion=("开启“最大瘦身”转 OGG 收益更大。" if apk_remap
                            else "默认档会按当前码率档重编。"),
            ))
            entry.est_saving = int(a.size * (0.35 if apk_remap and a.ext == ".mp3"
                                             else 0.2))
        return entry

    if a.ext == ".wav":
        entry.priority = Priority.HIGH if kb > 512 else Priority.MEDIUM
        entry.issues.append(Issue(
            message=f"WAV 是无压缩格式，体积大（{_fmt_size(a.size)}）。",
            suggestion="转成 OGG 格式通常能缩小 80% 以上，Ren'Py 对 OGG 支持最好。",
        ))
        entry.est_saving = int(a.size * 0.85)
    elif a.ext == ".mp3" and mode == "project":
        if kb > 1024:
            entry.priority = Priority.MEDIUM
        entry.issues.append(Issue(
            message="MP3 在 Ren'Py 中不如 OGG 原生（循环播放等特性）。",
            suggestion="建议转成 OGG，顺便控制码率。",
        ))
        entry.est_saving = int(a.size * 0.3)
    elif a.ext == ".ogg" and a.bitrate and a.bitrate > cfg.HIGH_AUDIO_BITRATE:
        entry.priority = Priority.LOW
        entry.issues.append(Issue(
            message=f"OGG 码率偏高（{a.bitrate} kbps），对游戏音频来说有浪费。",
            suggestion="重编码到 128 kbps 左右，人耳几乎无感。",
        ))
        entry.est_saving = int(a.size * 0.3)

    if kb >= cfg.LARGE_AUDIO_KB and entry.priority == Priority.LOW:
        entry.priority = Priority.MEDIUM
        entry.issues.append(Issue(
            message=f"音频体积较大（{_fmt_size(a.size)}）。",
            suggestion="检查是否可以降低码率或裁剪长度。",
        ))
    return entry


def _analyze_font(a: AssetInfo, mode: str = "project") -> AnalysisEntry:
    entry = AnalysisEntry(asset=a)
    kb = _kb(a.size)
    if mode == "apk":
        # 与 slim_apk 的实际处理门槛一致：256KB 以上的 ttf/otf 才做字体瘦身
        if a.ext in (".ttf", ".otf") and kb >= 256:
            entry.priority = Priority.HIGH if kb >= 1024 else Priority.MEDIUM
            entry.issues.append(Issue(
                message=f"字体可瘦身（{_fmt_size(a.size)}）。",
                suggestion="只保留游戏实际用到的字符，通常能减 60~80%。",
            ))
            entry.est_saving = int(a.size * (0.8 if kb >= 1024 else 0.6))
        return entry
    if a.ext in (".ttc", ".otc"):
        entry.issues.append(Issue(
            message="字体集合文件（TTC/OTC）当前版本不支持自动瘦身。",
            suggestion="可先用外部工具拆成单个 TTF，再交给本工具处理。",
        ))
        return entry
    if kb >= cfg.LARGE_FONT_KB:
        entry.priority = Priority.HIGH
        entry.issues.append(Issue(
            message=f"字体体积大（{_fmt_size(a.size)}），通常包含数万个用不到的字形。",
            suggestion="字体瘦身：只保留项目实际用到的字，通常能减 80% 以上。",
        ))
        entry.est_saving = int(a.size * 0.8)
    elif kb >= 1024:
        entry.priority = Priority.MEDIUM
        entry.issues.append(Issue(
            message=f"字体有一定瘦身空间（{_fmt_size(a.size)}）。",
            suggestion="建议做字体瘦身，只保留实际用到的字符。",
        ))
        entry.est_saving = int(a.size * 0.6)
    return entry


def _analyze_video(a: AssetInfo) -> AnalysisEntry:
    entry = AnalysisEntry(asset=a)
    kb = _kb(a.size)
    if kb >= cfg.LARGE_VIDEO_KB:
        entry.priority = Priority.MEDIUM
        entry.issues.append(Issue(
            message=f"视频体积较大（{_fmt_size(a.size)}）。",
            suggestion="视频转码兼容性风险最高，本版本暂不自动处理（二期功能），请手动评估。",
        ))
    return entry


def analyze(assets: list[AssetInfo], root: str, mode: str,
            apk_remap: bool = False) -> AnalysisReport:
    """mode: "project"（工程）/ "dist"（成品）/ "apk"（APK 只读预分析）。

    apk_remap 仅对 mode="apk" 有意义：开启后按“最大瘦身”口径估算。
    """
    report = AnalysisReport(root=root, mode=mode)
    for a in assets:
        report.total_size += a.size
        if a.kind == AssetKind.IMAGE:
            entry = _analyze_image(a, mode, apk_remap)
        elif a.kind == AssetKind.AUDIO:
            entry = _analyze_audio(a, mode, apk_remap)
        elif a.kind == AssetKind.FONT:
            entry = _analyze_font(a, mode)
        elif a.kind == AssetKind.VIDEO:
            entry = _analyze_video(a)
        else:
            continue
        report.entries.append(entry)

    # 按 优先级 > 预计节省体积 排序，最值得处理的排最前
    order = {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}
    report.entries.sort(key=lambda e: (order[e.priority], -(e.est_saving or 0)))
    return report
