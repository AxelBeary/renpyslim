"""外部程序调用工具：统一静默运行。

Windows 上调用控制台程序（ffprobe / ffmpeg / 7z 等）如果不加
CREATE_NO_WINDOW，每次都会闪一个黑框——扫描几百个音频就是
几百次闪烁，会把普通用户吓到。所有 subprocess 调用走这里。
"""
from __future__ import annotations

import subprocess
import sys

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # 仅 Windows 有


def run_quiet(cmd, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run 的静默版：Windows 下不弹控制台窗口。"""
    if sys.platform == "win32":
        kwargs.setdefault("creationflags", _NO_WINDOW)
    return subprocess.run(cmd, **kwargs)
