"""数据模型：所有引擎模块共享的结构定义。"""
from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Optional


class AssetKind(str, enum.Enum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    FONT = "font"
    OTHER = "other"


class Priority(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".avif"}
AUDIO_EXTS = {".wav", ".mp3", ".ogg", ".opus", ".flac", ".m4a"}
VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".avi", ".mov", ".ogv", ".m1v"}
FONT_EXTS = {".ttf", ".otf", ".ttc", ".otc", ".woff", ".woff2"}
SCRIPT_EXTS = {".rpy", ".rpym", ".py"}
TEXT_EXTS = SCRIPT_EXTS | {".txt", ".json", ".csv"}

# 优化时永远跳过的目录（缓存、版本控制、发布产物等）
SKIP_DIRS = {"saves", "cache", ".git", "__pycache__", "tmp", "log", "errors.txt"}


def kind_of(ext: str) -> AssetKind:
    ext = ext.lower()
    if ext in IMAGE_EXTS:
        return AssetKind.IMAGE
    if ext in AUDIO_EXTS:
        return AssetKind.AUDIO
    if ext in VIDEO_EXTS:
        return AssetKind.VIDEO
    if ext in FONT_EXTS:
        return AssetKind.FONT
    return AssetKind.OTHER


@dataclass
class AssetInfo:
    """一个被扫描到的资源文件。"""
    path: str                     # 绝对路径
    rel: str                      # 相对扫描根目录的路径（统一用 / 分隔）
    kind: AssetKind
    size: int                     # 字节
    width: Optional[int] = None   # 图片宽
    height: Optional[int] = None  # 图片高
    duration: Optional[float] = None   # 音视频时长（秒）
    bitrate: Optional[int] = None      # 音频码率（kbps）
    in_rpa: bool = False          # 是否来自 RPA 封包（模式 B）
    rpa_name: Optional[str] = None

    @property
    def ext(self) -> str:
        return Path(self.path).suffix.lower()


@dataclass
class Issue:
    """分析报告中针对单个资源的一条问题/建议。"""
    message: str        # 可能的问题（人话）
    suggestion: str     # 优化建议（人话）


@dataclass
class AnalysisEntry:
    asset: AssetInfo
    issues: list[Issue] = field(default_factory=list)
    priority: Priority = Priority.LOW
    est_saving: Optional[int] = None  # 预计可省字节数（估算）

    def to_dict(self) -> dict:
        d = asdict(self)
        d["asset"]["kind"] = self.asset.kind.value
        d["priority"] = self.priority.value
        return d


@dataclass
class AnalysisReport:
    root: str
    mode: str                          # "project" 或 "dist"
    total_size: int = 0
    entries: list[AnalysisEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)   # 全局级提醒（如检测到动态输入）
    charset_size: Optional[int] = None                  # 扫描到的唯一字符数

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "mode": self.mode,
            "total_size": self.total_size,
            "charset_size": self.charset_size,
            "warnings": self.warnings,
            "entries": [e.to_dict() for e in self.entries],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@dataclass
class ChangeRecord:
    """修改清单的一条记录：改了什么、从哪到哪。"""
    action: str          # compress / convert / subset_font / rename_ref / backup / delete / rpa_rebuild
    src: str
    dst: str = ""
    detail: str = ""
    ref_file: str = ""   # 引用改写发生的位置（文件）
    ref_line: int = 0    # 引用改写发生的位置（行号）

    def to_dict(self) -> dict:
        return asdict(self)


class Progress:
    """进度回调包装：流水线各阶段通过它汇报进度与日志。"""

    def __init__(self, callback: Optional[Callable[[str, str], None]] = None):
        # callback(stage, message)，stage 形如 "analyze" / "optimize" / "package"
        self._cb = callback

    def emit(self, stage: str, message: str) -> None:
        if self._cb:
            try:
                self._cb(stage, message)
            except Exception:
                pass

    def child(self) -> "Progress":
        return self
