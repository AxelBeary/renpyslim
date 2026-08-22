"""外部程序调用工具：统一静默运行。

Windows 上调用控制台程序（ffprobe / ffmpeg / 7z 等）如果不加
CREATE_NO_WINDOW，每次都会闪一个黑框——扫描几百个音频就是
几百次闪烁，会把普通用户吓到。所有 subprocess 调用走这里。
"""
from __future__ import annotations

import subprocess
import sys
import threading

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # 仅 Windows 有

# 审核修复（中-3）：登记运行中的子进程，用户取消时可整体杀掉，
# 否则 ThreadPoolExecutor 退出要等 ffmpeg 自然跑完（视频上限 7200s）
_ACTIVE: set = set()
_ACTIVE_LOCK = threading.Lock()


def _kill_tree(proc) -> None:
    """杀掉指定进程（Windows 上杀整棵进程树）。

    .bat→cmd→java 这类链式启动只杀直接进程会留下孤儿：
    超时/取消后 java 继续占用文件。故 Windows 一律用
    taskkill /T /F /PID 连树拔除；非 Windows 保持 kill()。
    """
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=_NO_WINDOW, timeout=15)
        else:
            proc.kill()
    except Exception:
        pass


def run_quiet(cmd, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run 的静默版：Windows 下不弹控制台窗口。

    审核修复（中-17）：统一 stdin=DEVNULL——加密 RAR 无密码时
    7-Zip 会停在终端等输入，不隔离 stdin 会挂满超时。
    """
    capture = kwargs.pop("capture_output", False)
    if capture:
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    if sys.platform == "win32":
        kwargs.setdefault("creationflags", _NO_WINDOW)
    timeout = kwargs.pop("timeout", None)
    with subprocess.Popen(cmd, **kwargs) as proc:
        with _ACTIVE_LOCK:
            _ACTIVE.add(proc)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # 第二波修复：只杀直接进程会留下链式孤儿（apksigner.bat→cmd→java）
            # 继续占文件跑满时长，改与 kill_children 相同的杀树逻辑
            _kill_tree(proc)
            out, err = proc.communicate()
            raise
        finally:
            with _ACTIVE_LOCK:
                _ACTIVE.discard(proc)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def kill_children() -> None:
    """杀掉本工具启动的全部外部程序（取消任务时调用）。

    Windows 上用 taskkill /T 杀整棵进程树：.bat→cmd→java 这类
    链式启动只杀父进程会留下孤儿继续占用文件（审核修复 中-3/中-15）。
    """
    with _ACTIVE_LOCK:
        procs = list(_ACTIVE)
    for proc in procs:
        _kill_tree(proc)
