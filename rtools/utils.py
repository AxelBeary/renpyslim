"""通用小工具：各处共用的小函数归一到这里，避免复制粘贴。"""
from __future__ import annotations

from typing import Optional


def fmt_size(size: Optional[int]) -> str:
    """把字节数格式成人话体积。None 显示为 —。"""
    if size is None:
        return "—"
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    return f"{size / 1024:.0f} KB"
