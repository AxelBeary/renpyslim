"""扫描器：遍历工程或成品目录，收集资源文件清单与元数据。

扫描分两步：先快速数出有多少资源（给进度条一个总数），
再逐个读取元数据并汇报进度。所有外部程序调用静默无窗口。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable, Iterator, Optional

from .models import (
    AssetInfo, AssetKind, SKIP_DIRS, kind_of,
    IMAGE_EXTS, AUDIO_EXTS, VIDEO_EXTS, FONT_EXTS,
)
from .procutil import run_quiet
from . import rpa

ASSET_EXTS = IMAGE_EXTS | AUDIO_EXTS | VIDEO_EXTS | FONT_EXTS

_FFPROBE = shutil.which("ffprobe")

# 进度回调签名：fn(已完成数, 总数, 当前文件相对路径)
ScanProgress = Optional[Callable[[int, int, str], None]]


def _progress_step(total: int) -> int:
    """小项目逐个报、大项目抽样报，兼顾实时感与性能。"""
    if total <= 50:
        return 1
    if total <= 500:
        return 5
    return 20


def _probe_media(path: str) -> tuple[Optional[float], Optional[int]]:
    """用 ffprobe 读取时长和音频码率；不可用时返回 (None, None)。"""
    if not _FFPROBE:
        return None, None
    try:
        out = run_quiet(
            [_FFPROBE, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, timeout=30,
        )
        data = json.loads(out.stdout.decode("utf-8", "replace"))
        duration = None
        bitrate = None
        fmt = data.get("format", {})
        if fmt.get("duration"):
            duration = float(fmt["duration"])
        for s in data.get("streams", []):
            if s.get("codec_type") == "audio" and s.get("bit_rate"):
                bitrate = int(int(s["bit_rate"]) / 1000)
                break
        return duration, bitrate
    except Exception:
        return None, None


def _image_size(path: str) -> tuple[Optional[int], Optional[int]]:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.width, im.height
    except Exception:
        return None, None


def iter_files(root: Path) -> Iterator[Path]:
    """递归遍历目录，跳过缓存类目录。"""
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        yield p


def scan_assets(root: str, probe: bool = True,
                progress: ScanProgress = None) -> list[AssetInfo]:
    """扫描散落的资源文件。probe=True 时读取图片尺寸/音视频元数据。"""
    root_p = Path(root)
    candidates = []
    for p in iter_files(root_p):
        kind = kind_of(p.suffix.lower())
        if kind != AssetKind.OTHER:
            candidates.append((p, kind))

    total = len(candidates)
    step = _progress_step(total)
    results: list[AssetInfo] = []
    for i, (p, kind) in enumerate(candidates, start=1):
        rel = p.relative_to(root_p).as_posix()
        if progress and (i % step == 1 or i == total):
            progress(i, total, rel)
        try:
            size = p.stat().st_size
        except OSError:
            continue
        info = AssetInfo(path=str(p), rel=rel, kind=kind, size=size)
        if probe:
            if kind == AssetKind.IMAGE:
                info.width, info.height = _image_size(str(p))
            elif kind in (AssetKind.AUDIO, AssetKind.VIDEO):
                info.duration, info.bitrate = _probe_media(str(p))
        results.append(info)
    return results


def scan_rpa_assets(root: str, extract_dir: str, probe: bool = True,
                    progress: ScanProgress = None) -> list[AssetInfo]:
    """扫描成品目录内的 RPA 封包，把封包里的资源解出到 extract_dir 并登记。

    返回的 AssetInfo.rel 使用封包内路径（即游戏内引用路径）。
    """
    root_p = Path(root)
    extract_p = Path(extract_dir)

    rpa_files = [p for p in iter_files(root_p) if p.suffix.lower() == ".rpa"]
    archives: list[tuple[Path, rpa.RpaArchive]] = []
    plan: list[tuple[Path, rpa.RpaArchive, str]] = []   # (rpa路径, 封包, 内部名)
    try:
        for p in rpa_files:
            try:
                arc = rpa.RpaArchive(str(p))
            except rpa.RpaError:
                continue
            archives.append((p, arc))
            for name in arc.names():
                if kind_of(Path(name).suffix.lower()) != AssetKind.OTHER:
                    plan.append((p, arc, name))

        total = len(plan)
        step = _progress_step(total)
        results: list[AssetInfo] = []
        for i, (p, arc, name) in enumerate(plan, start=1):
            if progress and (i % step == 1 or i == total):
                progress(i, total, f"{p.stem}/{name}")
            ext = Path(name).suffix.lower()
            kind = kind_of(ext)
            out = extract_p / p.stem / name
            out.parent.mkdir(parents=True, exist_ok=True)
            try:
                arc.extract(name, str(out))
            except rpa.RpaError:
                continue
            info = AssetInfo(
                path=str(out), rel=name, kind=kind,
                size=out.stat().st_size,
                in_rpa=True, rpa_name=p.name,
            )
            if probe and kind == AssetKind.IMAGE:
                info.width, info.height = _image_size(str(out))
            results.append(info)
        return results
    finally:
        for _, arc in archives:
            arc.close()
