"""CLI 中断契约（issue #40）：Ctrl+C 也要有 JSON 出口。

契约：打包 / 优化过程被用户中断时，stdout 仍是合法结果 JSON（ok:false、
带 interrupted 标记与 kind / command），退出码 130（128 + SIGINT），
既不与"失败 / 参数错误 = 1"混淆，也不再以 -2（SIGINT）裸奔收场。
stderr 仍是人话中文日志；任务历史沿用既有约定收尾为 error；不落崩溃转储。

测试路线：Windows 不能向任意子进程投递 Ctrl+C（是 TerminateProcess 而非信号），
所以主体用例跑在真 CLI 子进程里，用四种方式制造中断——任务体抛
KeyboardInterrupt、取消标志触发 PipelineCancelled、进程内 raise_signal 走
真实的信号处理器链路、扫描段的 ScanCancelled；真实跨进程 SIGINT 投递
另有一条 POSIX-only 用例。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

# 驱动器：一个真实的 CLI 进程，只把流水线换成"被打断"的桩，
# 其余（SIGINT 处理器、历史落库、finally 兜底、JSON 出口）全是真的。
_DRIVER = '''
import signal
import sys

ROOT, target, outcome, work_root, output_dir = sys.argv[1:6]
sys.path.insert(0, ROOT)

import cli


def _keyboard(*a, **kw):
    raise KeyboardInterrupt


def _cancelled(*a, **kw):
    # Ctrl+C 的常规路径：信号处理器只置取消标志，流水线在检查点抛取消
    raise cli.pipeline.PipelineCancelled()


def _signal(*a, **kw):
    # 进程内的"真信号"：走同一个 SIGINT 处理器，再走同一条取消路径
    cancel = kw.get("cancel")
    signal.raise_signal(signal.SIGINT)
    if cancel and cancel():
        raise cli.pipeline.PipelineCancelled()
    raise AssertionError("SIGINT 处理器没有置位取消标志")


def _scan_cancelled(*a, **kw):
    # 扫描段按 Ctrl+C：scanner 抛自己的取消异常（旧版落进"意外错误"）
    from rtools import scanner
    raise scanner.ScanCancelled()


_STUBS = {"keyboard": _keyboard, "cancelled": _cancelled, "signal": _signal}
if outcome == "scan":
    cli.scanner.scan_assets = _scan_cancelled
else:
    cli.pipeline.run_project = _STUBS[outcome]

sys.exit(cli.main(["optimize", target,
                   "--work-root", work_root, "--output", output_dir]))
'''

# POSIX 专用：待在流水线里等外部真信号（Windows 无法跨进程投递 Ctrl+C）。
_SIGNAL_WAIT_DRIVER = '''
import sys
import time
from pathlib import Path

ROOT, target, work_root, output_dir, ready = sys.argv[1:6]
sys.path.insert(0, ROOT)

import cli


def _wait_for_cancel(*a, **kw):
    cancel = kw.get("cancel")
    Path(ready).write_text("ready", encoding="utf-8")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if cancel and cancel():
            raise cli.pipeline.PipelineCancelled()
        time.sleep(0.02)
    raise AssertionError("60 秒内没有收到取消信号")


cli.pipeline.run_project = _wait_for_cancel
sys.exit(cli.main(["optimize", target,
                   "--work-root", work_root, "--output", output_dir]))
'''

# full 的打包段：issue #40 报的就是"打包过程中按 Ctrl+C"。
# 桩里先核对 SIGINT 已从取消标志的处理器交还默认处理器（否则按下去没反应），
# 再以 KeyboardInterrupt 模拟用户按键。
_FULL_DRIVER = '''
import signal
import sys

ROOT, target, work_root, output_dir = sys.argv[1:5]
sys.path.insert(0, ROOT)

import cli


def _optimize(*a, **kw):
    return {"report": "r.json", "changelog": "c.json",
            "working_dir": str(target)}


def _package(*a, **kw):
    current = signal.getsignal(signal.SIGINT)
    print(f"SIGINT_HANDLER_OK={current is signal.default_int_handler}",
          file=sys.stderr)
    raise KeyboardInterrupt


cli.pipeline.run_project = _optimize
cli.packager.find_sdk = lambda sdk: "/fake/sdk"
cli.packager.package_project = _package

sys.exit(cli.main(["full", target, "--platforms", "pc",
                   "--work-root", work_root, "--output", output_dir]))
'''


def _make_project(tmp_path: Path) -> Path:
    """带 .rpy 源码的最小工程：optimize 会判成 project，走 run_project。"""
    target = tmp_path / "MyGame"
    (target / "game").mkdir(parents=True)
    (target / "game" / "script.rpy").write_text("label start:\n    pass\n",
                                                encoding="utf-8")
    return target


def _env(tmp_path: Path) -> dict:
    """历史 / 家目录全指到临时目录，绝不碰用户真实记录。"""
    env = dict(os.environ)
    for key in ("RENPYSLIM_HOME", "HOME", "USERPROFILE"):
        env[key] = str(tmp_path)
    return env


def _run_driver(tmp_path: Path, source: str, name: str, *args: str,
                timeout: int = 180) -> subprocess.CompletedProcess:
    script = tmp_path / name
    script.write_text(source, encoding="utf-8")
    return subprocess.run([PY, str(script), str(ROOT), *args],
                          capture_output=True, timeout=timeout,
                          check=False, cwd=str(ROOT), env=_env(tmp_path))


def _jobs(tmp_path: Path) -> list:
    """读驱动器进程落下的历史（RENPYSLIM_HOME 已指到 tmp_path）。"""
    data = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    return data["jobs"] if isinstance(data, dict) else data


@pytest.mark.parametrize("outcome,history_hint", [
    ("keyboard", "KeyboardInterrupt"),   # 任务体里抛 KeyboardInterrupt
    ("cancelled", "用户取消"),            # 取消标志触发 PipelineCancelled
    ("signal", "用户取消"),               # 真 SIGINT 处理器 → 取消标志
    ("scan", "用户取消扫描"),             # 扫描段的 ScanCancelled（旧版报"意外错误"）
])
def test_interrupt_emits_json_and_exits_130(tmp_path, outcome, history_hint):
    """中断同样以结果 JSON 收尾：ok:false + interrupted，退出码 130。"""
    target = _make_project(tmp_path)
    proc = _run_driver(tmp_path, _DRIVER, "driver.py", str(target), outcome,
                       str(tmp_path / "work"), str(tmp_path / "out"))

    assert proc.returncode == 130, proc.stderr.decode("utf-8", "replace")
    # 整段 stdout 必须是一个 JSON：有日志漏进来这一步就会炸
    data = json.loads(proc.stdout.decode("utf-8"))
    assert data["ok"] is False
    assert data["error"] == "用户已中断"
    assert data["interrupted"] is True              # 机器可读的中断标记
    assert data["kind"] == "optimize"               # 与任务历史同名的定位字段
    assert data["command"] == "optimize"

    # stderr 仍是人话中文日志，不是 JSON
    stderr = proc.stderr.decode("utf-8", "replace")
    assert "用户已中断" in stderr

    # 历史语义不变：中断记 error（既有约定），且不落崩溃转储
    jobs = _jobs(tmp_path)
    assert len(jobs) == 1
    assert jobs[0]["kind"] == "optimize"
    assert jobs[0]["status"] == "error"
    assert history_hint in (jobs[0]["error"] or "")
    crashes = tmp_path / "crashes"
    assert not crashes.exists() or not any(crashes.iterdir()), \
        "用户中断不该落崩溃转储"


def test_packaging_stage_interrupt_is_also_json(tmp_path):
    """issue #40 的原始场景：打包段按 Ctrl+C。

    取消回调只服务优化段（置标志 + 落部分清单）；打包是一条外部命令，
    没人查那个标志，继续留着处理器会把 Ctrl+C 静默吞掉——用户以为停了，
    SDK 还在后台写产物。所以打包段之前必须把 SIGINT 交还默认处理器，
    交还后由 `cli.main` 的中断出口收场（JSON + 130）。
    """
    target = _make_project(tmp_path)
    proc = _run_driver(tmp_path, _FULL_DRIVER, "full_driver.py", str(target),
                       str(tmp_path / "work"), str(tmp_path / "out"))

    assert proc.returncode == 130, proc.stderr.decode("utf-8", "replace")
    stderr = proc.stderr.decode("utf-8", "replace")
    assert "SIGINT_HANDLER_OK=True" in stderr, \
        "打包段必须已恢复默认 SIGINT 处理器，否则 Ctrl+C 会被吞掉"

    data = json.loads(proc.stdout.decode("utf-8"))
    assert data["ok"] is False
    assert data["interrupted"] is True
    assert data["kind"] == "full"
    assert data["command"] == "full"


def test_failure_still_exits_1_and_has_no_interrupt_marker(tmp_path):
    """失败路径不受新契约影响：仍是退出码 1、无 interrupted 标记。"""
    proc = subprocess.run(
        [PY, str(ROOT / "cli.py"), "optimize", str(tmp_path / "nope")],
        capture_output=True, timeout=180, check=False,
        cwd=str(ROOT), env=_env(tmp_path))
    assert proc.returncode == 1
    data = json.loads(proc.stdout.decode("utf-8"))
    assert data["ok"] is False
    assert "interrupted" not in data


@pytest.mark.skipif(sys.platform == "win32",
                    reason="POSIX 专属：真实 SIGINT 投递（Windows 只能 TerminateProcess）")
def test_real_sigint_ends_with_json_and_130(tmp_path):
    """真按 Ctrl+C 的跨进程链路：SIGINT → 取消标志 → 取消 → JSON + 130。"""
    target = _make_project(tmp_path)
    ready = tmp_path / "ready.flag"
    script = tmp_path / "signal_driver.py"
    script.write_text(_SIGNAL_WAIT_DRIVER, encoding="utf-8")
    proc = subprocess.Popen(
        [PY, str(script), str(ROOT), str(target), str(tmp_path / "work"),
         str(tmp_path / "out"), str(ready)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(ROOT), env=_env(tmp_path), start_new_session=True)
    try:
        deadline = time.monotonic() + 60
        while not ready.exists() and time.monotonic() < deadline:
            assert proc.poll() is None, "驱动器在收到信号前就退出了"
            time.sleep(0.02)
        assert ready.exists(), "驱动器没能进入流水线"
        proc.send_signal(signal.SIGINT)
        out, err = proc.communicate(timeout=180)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()

    assert proc.returncode == 130, err.decode("utf-8", "replace")
    data = json.loads(out.decode("utf-8"))
    assert data["ok"] is False
    assert data["interrupted"] is True
    assert data["error"] == "用户已中断"
