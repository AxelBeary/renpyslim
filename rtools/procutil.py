"""外部程序调用工具：统一静默运行。

Windows 上调用控制台程序（ffprobe / ffmpeg / 7z 等）如果不加
CREATE_NO_WINDOW，每次都会闪一个黑框——扫描几百个音频就是
几百次闪烁，会把普通用户吓到。所有 subprocess 调用走这里。

POSIX（v0.18.0 / issue #26）：外部工具普遍是“壳脚本 + 真进程”的链式启动
（`renpy.sh` → python、`gradle` → java、`apksigner` → java）。只杀直接子进程
会留下孙进程继续占用管道与文件，超时/取消后表现就是“卡住不返回”。
因此这里统一用 `start_new_session=True` 让子进程自成进程组，终止时对
**整个进程组**发 SIGTERM，宽限期后再 SIGKILL。
"""
from __future__ import annotations

import contextlib
import contextvars
import os
import signal
import subprocess
import sys
import threading
import time

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # 仅 Windows 有

# 终止进程组时 SIGTERM 到 SIGKILL 之间的宽限期（秒）
_KILL_GRACE = 3.0

# 审核修复（中-3）：登记运行中的子进程，用户取消时可整体杀掉，
# 否则 ThreadPoolExecutor 退出要等 ffmpeg 自然跑完（视频上限 7200s）。
# 2026-11-27 交叉审计：登记时带上“任务归属”令牌——取消一个任务只能
# 杀它自己的子进程，不能误伤并发的只读分析（scan_assets 的 ffprobe）。
_ACTIVE: dict = {}
_ACTIVE_LOCK = threading.Lock()

# 当前任务令牌；Web 每个 job 进入时设一个，pipeline 的取消收尾按它筛选。
_SCOPE: contextvars.ContextVar = contextvars.ContextVar(
    "renpyslim_proc_scope", default=None)

# POSIX：由本模块以“新会话”启动的子进程 pid 集合——只有这些才允许
# os.killpg。拿不准就别按组杀，否则可能连自己所在的进程组一起端了。
_OWN_SESSION_PIDS: set = set()

# kill_children() 的默认哨兵：不传 scope 时保持“全杀”的旧语义
_ALL_SCOPES = object()


@contextlib.contextmanager
def task_scope():
    """给一段任务代码打上令牌，期间启动的外部进程都归这个任务。

    不设令牌（CLI、直接调用 rtools）时所有进程归属 None，
    kill_children(scope=None) 就是旧行为（全杀）。
    """
    token = object()
    reset = _SCOPE.set(token)
    try:
        yield token
    finally:
        _SCOPE.reset(reset)


def current_scope():
    """当前线程/上下文的任务令牌，没有则为 None。"""
    return _SCOPE.get()


def _kill_tree(proc) -> None:
    """杀掉指定进程（Windows 杀整棵进程树，POSIX 杀整个进程组）。

    Windows：.bat→cmd→java 这类链式启动只杀直接进程会留下孤儿，
    故一律用 taskkill /T /F /PID 连树拔除。
    POSIX：子进程由本模块以 start_new_session=True 启动，pgid == pid，
    对进程组先 SIGTERM 再 SIGKILL，`renpy.sh` → python 这种链同样清干净。
    """
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=_NO_WINDOW, timeout=15)
        elif proc.pid in _OWN_SESSION_PIDS:
            # 直接子进程可能已退出、孙进程仍活着占着管道；poll() 非空
            # 也不能提前返回，否则超时收尾会把管道一直挂住。
            _kill_process_group(proc.pid)
        elif proc.poll() is not None:
            return                      # 已经退出，无需再杀
        else:
            proc.kill()
    except Exception:
        pass


def _kill_process_group(pgid: int) -> None:
    """先 SIGTERM 给个收尾机会，宽限期后 SIGKILL 兜底。"""
    def _send(sig) -> bool:
        try:
            os.killpg(pgid, sig)
            return True
        except ProcessLookupError:
            return False                # 组里已经没人了
        except OSError:
            return False

    _send(signal.SIGTERM)
    deadline = time.monotonic() + _KILL_GRACE
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)          # 探活：组里还有活人就继续等
        except ProcessLookupError:
            return
        except OSError:
            return
        time.sleep(0.05)
    _send(signal.SIGKILL)


def run_quiet(cmd, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run 的静默版：Windows 下不弹控制台窗口。

    审核修复（中-17）：统一 stdin=DEVNULL——加密 RAR 无密码时
    7-Zip 会停在终端等输入，不隔离 stdin 会挂满超时。

    POSIX：默认 start_new_session=True，让每个外部工具自成进程组，
    超时/取消时能连孙进程一起清掉（issue #26）。调用方显式传了
    start_new_session / preexec_fn 就尊重调用方，不按组杀。
    """
    capture = kwargs.pop("capture_output", False)
    if capture:
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    own_session = False
    if sys.platform == "win32":
        kwargs.setdefault("creationflags", _NO_WINDOW)
    elif "start_new_session" not in kwargs and "preexec_fn" not in kwargs:
        kwargs["start_new_session"] = True
        own_session = True
    timeout = kwargs.pop("timeout", None)
    with subprocess.Popen(cmd, **kwargs) as proc:
        with _ACTIVE_LOCK:
            _ACTIVE[proc] = _SCOPE.get()
            if own_session:
                _OWN_SESSION_PIDS.add(proc.pid)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # 第二波修复：只杀直接进程会留下链式孤儿（apksigner.bat→cmd→java）
            # 继续占文件跑满时长，改与 kill_children 相同的杀树逻辑
            _kill_tree(proc)
            # 孙进程若仍持管道，无超时的 communicate 会一直挂着——
            # 收尾也必须带超时，拿不到输出就按空处理（上面已 raise）。
            try:
                out, err = proc.communicate(timeout=_KILL_GRACE * 2)
            except subprocess.TimeoutExpired:
                if proc.poll() is None:
                    proc.kill()
                try:
                    proc.wait(timeout=_KILL_GRACE)
                except subprocess.TimeoutExpired:
                    pass
                out, err = b"", b""
            raise
        except BaseException:
            # Ctrl+C / SystemExit 等也要把外部工具收掉，否则 SDK/ffmpeg
            # 会在后台继续写产物，用户以为任务已停。
            _kill_tree(proc)
            try:
                proc.communicate(timeout=_KILL_GRACE)
            except Exception:
                pass
            raise
        finally:
            with _ACTIVE_LOCK:
                _ACTIVE.pop(proc, None)
                _OWN_SESSION_PIDS.discard(proc.pid)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def kill_children(scope=_ALL_SCOPES) -> None:
    """杀掉本工具启动的外部程序（取消任务时调用）。

    scope 缺省 = 旧行为（全杀，CLI 单任务足够）；
    Web 传当前任务令牌时只杀该任务自己的子进程，并发只读分析不被误伤。

    Windows 上用 taskkill /T 杀整棵进程树：.bat→cmd→java 这类
    链式启动只杀父进程会留下孤儿继续占用文件（审核修复 中-3/中-15）。
    POSIX 上对每个自建会话的进程组发信号，`renpy.sh` → python 同款处理。
    """
    with _ACTIVE_LOCK:
        procs = [proc for proc, owner in _ACTIVE.items()
                 if scope is _ALL_SCOPES or owner == scope]
    for proc in procs:
        _kill_tree(proc)
