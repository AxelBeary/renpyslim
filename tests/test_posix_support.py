"""POSIX 平台适配回归（issue #26）。

覆盖三类"非 Windows 会静默坏掉"的点：
1. Web 的文件路径校验 `_local_path`——旧版靠盘符，POSIX 绝对路径全被拒。
2. 桌面打开（reveal）——旧版直接调 ctypes.windll，非 Windows 必崩。
3. 外部工具进程树终止——POSIX 要按进程组杀，否则孙进程继续占管道。

这些测试在 Windows 上同样要过：断言按平台分支，不假设宿主系统。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rtools import procutil, sdk

web_app = pytest.importorskip("web.app")
app_mod = web_app


# ---------------------------------------------------------------------------
# 1. _local_path：POSIX 绝对路径必须通过，越界/相对/网络仍拒绝
# ---------------------------------------------------------------------------

def test_local_path_accepts_absolute_on_every_platform(tmp_path):
    p, err = app_mod._local_path(str(tmp_path), must_exist=True)
    assert err is None, err
    assert p == tmp_path


def test_local_path_folds_relative_segments(tmp_path):
    p, err = app_mod._local_path(str(tmp_path / "a" / ".." / "b"),
                                 must_exist=False)
    assert err is None, err
    assert p == tmp_path / "b"


def test_local_path_rejects_relative_nul_and_missing(tmp_path):
    assert app_mod._local_path("relative/game", must_exist=False)[1]
    assert app_mod._local_path(str(tmp_path) + "\x00evil",
                               must_exist=False)[1]
    assert app_mod._local_path(str(tmp_path / "nope"), must_exist=True)[1]


@pytest.mark.skipif(os.name == "nt", reason="POSIX 专属：根语义")
def test_local_path_posix_root_semantics():
    """POSIX 下唯一合法根是 "/"：任何绝对路径都在根之下，
    相对路径与畸形输入必须被拦在根之外。"""
    assert app_mod._local_path("/", must_exist=True)[0] == Path("/")
    assert app_mod._local_path("/tmp", must_exist=False)[1] is None
    # 网络形态在 allow_network=False 时仍然拒绝（与 Windows 同一口径）
    assert app_mod._local_path("//server/share/game", must_exist=False,
                               allow_network=False)[1]


def test_local_path_network_flag_still_blocks_windows_unc():
    assert app_mod._local_path(r"\\server\share\game", must_exist=False,
                               allow_network=False)[1]


# ---------------------------------------------------------------------------
# 2. reveal：非 Windows 走桌面分支，且文案不写死"资源管理器"
# ---------------------------------------------------------------------------

def test_desktop_name_matches_platform():
    expected = {"nt": "资源管理器", "darwin": "访达"}.get(os.name, "文件管理器")
    assert app_mod._DESKTOP_NAME == expected


@pytest.mark.skipif(os.name == "nt", reason="POSIX 专属：桌面打开分支")
def test_open_in_desktop_requires_graphical_session(tmp_path, monkeypatch):
    """没有 DISPLAY/WAYLAND_DISPLAY 时必须明确失败，不能假装成功。"""
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    with pytest.raises(OSError):
        app_mod._open_in_desktop(tmp_path, select=False)


@pytest.mark.skipif(os.name == "nt", reason="POSIX 专属：桌面打开分支")
def test_open_in_explorer_delegates_on_posix(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(app_mod, "_open_in_desktop",
                        lambda target, *, select: calls.append((target, select)))
    app_mod._open_in_explorer(tmp_path, select=True)
    assert calls == [(tmp_path, True)]


# ---------------------------------------------------------------------------
# 3. 进程树终止：POSIX 按进程组杀，超时后孙进程不得残留
# ---------------------------------------------------------------------------

def test_run_quiet_still_runs_normally():
    r = procutil.run_quiet([sys.executable, "-c", "print('hi')"],
                           capture_output=True, timeout=60)
    assert r.returncode == 0
    assert b"hi" in r.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 专属：进程组语义")
def test_posix_timeout_kills_whole_process_group(tmp_path):
    """壳脚本 → 孙进程 的链：只杀直接子进程时孙进程会继续跑。

    用一个会写"我跑到头了"标记文件的孙进程验证：超时后标记文件
    在宽限期内不得出现。
    """
    marker = tmp_path / "grandchild-finished"
    # sh -c 里再起一个 python 孙进程，睡 10 秒后写标记
    script = (
        f"{sys.executable} -c \"import time,pathlib;"
        f"time.sleep(10);pathlib.Path(r'{marker}').write_text('done')\""
    )
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        procutil.run_quiet(["sh", "-c", script], capture_output=True,
                           timeout=0.5)
    elapsed = time.monotonic() - t0
    assert elapsed < 8, f"超时处理挂起了 {elapsed:.1f}s，疑似孙进程未杀"
    time.sleep(1.0)          # 留出"如果没杀干净就会写标记"的窗口
    assert not marker.exists(), "孙进程仍在运行（进程组未被终止）"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 专属：进程组语义")
def test_kill_children_terminates_active_process(tmp_path):
    marker = tmp_path / "still-alive"
    script = (
        f"{sys.executable} -c \"import time,pathlib;"
        f"time.sleep(10);pathlib.Path(r'{marker}').write_text('x')\""
    )
    proc = procutil.run_quiet  # noqa: F841  (保持 import 使用点显式)
    import threading
    done = threading.Event()

    def worker():
        try:
            procutil.run_quiet(["sh", "-c", script], capture_output=True,
                               timeout=30)
        except Exception:
            pass
        finally:
            done.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    time.sleep(0.6)          # 等子进程真的起来并登记进 _ACTIVE
    procutil.kill_children()
    assert done.wait(8), "kill_children 没有让运行中的外部程序退出"
    time.sleep(0.5)
    assert not marker.exists()


# ---------------------------------------------------------------------------
# 4. SDK 启动器解析：renpy.exe / renpy.sh 两端都要认
# ---------------------------------------------------------------------------

def _make_sdk(tmp_path: Path, name: str, *, mode: int = 0o755) -> Path:
    sdk_dir = tmp_path / "sdk"
    sdk_dir.mkdir(exist_ok=True)
    launcher = sdk_dir / name
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    try:
        os.chmod(launcher, mode)
    except OSError:
        pass                     # Windows 上只能设只读位，忽略
    return sdk_dir


def test_find_launcher_accepts_exe_only(tmp_path):
    """只有 renpy.exe 的目录照样算 SDK（跨平台备份/挂载盘常见）。"""
    sdk_dir = _make_sdk(tmp_path, "renpy.exe")
    assert Path(sdk.find_launcher(str(sdk_dir))).name == "renpy.exe"


def test_find_launcher_falls_back_to_sh(tmp_path):
    sdk_dir = _make_sdk(tmp_path, "renpy.sh")
    assert Path(sdk.find_launcher(str(sdk_dir))).name == "renpy.sh"


def _make_sdk_with_both(tmp_path: Path) -> Path:
    """官方 Linux SDK 的真实布局：renpy.exe 与 renpy.sh 并存。"""
    sdk_dir = tmp_path / "sdk"
    sdk_dir.mkdir(exist_ok=True)
    (sdk_dir / "renpy.exe").write_bytes(b"FAKE-PE")
    sh = sdk_dir / "renpy.sh"
    sh.write_text("#!/bin/sh\n", encoding="utf-8")
    try:
        os.chmod(sh, 0o755)
    except OSError:
        pass                     # Windows 上只能设只读位，忽略
    return sdk_dir


def test_find_launcher_prefers_native_when_both_present(tmp_path):
    """两启动器并存时按平台选原生启动器（官方 Linux SDK 就是这种布局）。"""
    sdk_dir = _make_sdk_with_both(tmp_path)
    expected = "renpy.exe" if os.name == "nt" else "renpy.sh"
    assert Path(sdk.find_launcher(str(sdk_dir))).name == expected


@pytest.mark.skipif(os.name == "nt", reason="POSIX 专属：避免选中跑不起来的 exe")
def test_require_launcher_uses_sh_when_both_present(tmp_path):
    sdk_dir = _make_sdk_with_both(tmp_path)
    assert Path(sdk.require_launcher(str(sdk_dir))).name == "renpy.sh"


def test_find_launcher_returns_none_for_empty_dir(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert sdk.find_launcher(str(empty)) is None
    assert sdk.find_launcher(None) is None


def test_require_launcher_raises_with_human_message(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError) as exc:
        sdk.require_launcher(str(empty))
    # 提示里必须点名"本平台该有哪个启动器"
    assert "renpy.exe" in str(exc.value) or "renpy.sh" in str(exc.value)


@pytest.mark.skipif(os.name == "nt", reason="POSIX 专属：执行位语义")
def test_require_launcher_rejects_non_executable_sh(tmp_path):
    sdk_dir = _make_sdk(tmp_path, "renpy.sh", mode=0o644)
    # 发现阶段照旧认得出来（"有没有 SDK"与"能不能执行"是两件事）
    assert sdk.find_launcher(str(sdk_dir)) is not None
    with pytest.raises(FileNotFoundError) as exc:
        sdk.require_launcher(str(sdk_dir))
    assert "chmod +x" in str(exc.value)
