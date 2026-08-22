"""扫描器：遍历工程或成品目录，收集资源文件清单与元数据。

扫描分两步：先快速数出有多少资源（给进度条一个总数），
再逐个读取元数据并汇报进度。所有外部程序调用静默无窗口。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Callable, Iterator, Optional

from .models import (
    AssetInfo, AssetKind, SKIP_DIRS, kind_of,
    IMAGE_EXTS, AUDIO_EXTS, VIDEO_EXTS, FONT_EXTS,
)
from .procutil import run_quiet
from .utils import safe_join
from . import rpa

ASSET_EXTS = IMAGE_EXTS | AUDIO_EXTS | VIDEO_EXTS | FONT_EXTS

# 字符集提取关心的脚本/文本扩展名（封包里属 OTHER，不会被当资源登记）
SCRIPT_EXTRACT_EXTS = {".rpyc", ".rpymc", ".rpy", ".txt"}

_ffprobe_cache: Optional[str] = None
_ffprobe_looked = False


def find_ffprobe() -> Optional[str]:
    """按顺序找 ffprobe：PATH -> 程序旁 bin 目录（与 find_ffmpeg 同目录）。

    找不到返回 None；结果模块级缓存（惰性查找，避免逐文件重复 which）。
    """
    global _ffprobe_cache, _ffprobe_looked
    if _ffprobe_looked:
        return _ffprobe_cache
    found = shutil.which("ffprobe")
    if not found:
        candidates = []
        if getattr(sys, "frozen", False):   # PyInstaller 打包后
            candidates.append(Path(sys.executable).parent / "bin" / "ffprobe.exe")
        candidates.append(Path(__file__).resolve().parent.parent / "bin" / "ffprobe.exe")
        candidates.append(Path(__file__).resolve().parent.parent / "bin" / "ffprobe")
        for c in candidates:
            if c.exists():
                found = str(c)
                break
    _ffprobe_cache = found
    _ffprobe_looked = True
    return _ffprobe_cache


# 进度回调签名：fn(已完成数, 总数, 当前文件相对路径)
ScanProgress = Optional[Callable[[int, int, str], None]]


class ScanCancelled(Exception):
    """扫描被用户取消。"""


def _progress_step(total: int) -> int:
    """小项目逐个报、大项目抽样报，兼顾实时感与性能。"""
    if total <= 50:
        return 1
    if total <= 500:
        return 5
    return 20


def _probe_media(path: str) -> tuple[Optional[float], Optional[int]]:
    """用 ffprobe 读取时长和音频码率；不可用时返回 (None, None)。"""
    ffprobe = find_ffprobe()
    if not ffprobe:
        return None, None
    try:
        out = run_quiet(
            [ffprobe, "-v", "quiet", "-print_format", "json",
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
        # 审核修复：部分容器（如 WAV）流级没有 bit_rate，回退到容器级，
        # 否则码率探测永远为 None，降码率判断全被跳过
        if bitrate is None and fmt.get("bit_rate"):
            bitrate = int(int(fmt["bit_rate"]) / 1000)
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
                progress: ScanProgress = None,
                cancel: Optional[Callable[[], bool]] = None) -> list[AssetInfo]:
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
        if cancel and cancel():
            raise ScanCancelled()
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
                    progress: ScanProgress = None,
                    cancel: Optional[Callable[[], bool]] = None,
                    extract_scripts: bool = False) -> list[AssetInfo]:
    """扫描成品目录内的 RPA 封包，把封包里的资源解出到 extract_dir 并登记。

    返回的 AssetInfo.rel 使用封包内路径（即游戏内引用路径）。
    extract_scripts=True 时一并解出脚本/文本条目（不登记为资源）：
    成品模式字符集提取需要它们，否则脚本封在 rpa 里时扫不到
    实际使用字符，字体会被剃成保底集。（审核修复）
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
                ext = Path(name).suffix.lower()
                if kind_of(ext) != AssetKind.OTHER:
                    plan.append((p, arc, name))
                elif extract_scripts and ext in SCRIPT_EXTRACT_EXTS:
                    # 只解出不登记；同名互覆无妨（字符集收集不怕重）
                    out = safe_join(extract_p / p.stem, name)
                    if out is None:
                        continue
                    out.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        arc.extract(name, str(out))
                    except rpa.RpaError:
                        continue

        total = len(plan)
        step = _progress_step(total)
        results: list[AssetInfo] = []
        for i, (p, arc, name) in enumerate(plan, start=1):
            if cancel and cancel():
                raise ScanCancelled()
            if progress and (i % step == 1 or i == total):
                progress(i, total, f"{p.stem}/{name}")
            ext = Path(name).suffix.lower()
            kind = kind_of(ext)
            # 审核修复：封包内路径来自不可信输入，净化后再落盘（防 zip-slip）
            out = safe_join(extract_p / p.stem, name)
            if out is None:
                continue
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
            if probe:
                if kind == AssetKind.IMAGE:
                    info.width, info.height = _image_size(str(out))
                elif kind in (AssetKind.AUDIO, AssetKind.VIDEO):
                    # 审核修复（中-19）：封包内音频也得探码率，否则成品
                    # 模式降码率判断（依赖 a.bitrate）对音频绝大多数
                    # 封在 rpa 里的主体场景永远失效（文件已解出，成本可控）
                    info.duration, info.bitrate = _probe_media(str(out))
            results.append(info)
        return results
    finally:
        for _, arc in archives:
            arc.close()
