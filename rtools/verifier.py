"""自动验证：优化完成后调用官方 SDK 的 lint 做静态检查。

lint 能查出引用断裂、图片缺失、脚本错误等问题，是"优化后仍能运行"
的第一道官方背书。启动级验证更彻底但耗时，保留给用户手动确认。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .procutil import run_quiet

# 错误行特征：带 "game/xxx.rpy:行号" 位置前缀。
# 审核修复（中-21）：旧筛法要求行内同时含 "error/错误" 与 ".rpy"，
# 而典型错误行 "game/script.rpy:41: ..." 不含 error 一词，
# 导致退出码为 0 但输出含错误时漏报；改为只认位置前缀。
_ERR_LINE_RE = re.compile(r"\.rpym?:\d+")


def _decode_pipe(raw: bytes) -> str:
    """解码子进程管道输出。

    审核修复（中-21）：中文 Windows 上 renpy 的控制台输出是
    GBK/cp936，硬按 UTF-8 解会乱码；先试 UTF-8，失败回退 cp936。
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp936", "replace")


def lint_project(sdk: str, project_dir: str, timeout: int = 600) -> dict:
    """对工程跑 renpy lint。返回 {ran, ok, summary, output, suspects}。

    ok 判定：进程正常退出，且输出里没有疑似错误行。
    审核修复（高-1）：所有返回分支统一带 suspects 键，
    超时/异常分支缺键曾让流水线收尾 KeyError 崩溃。
    """
    renpy_exe = Path(sdk) / "renpy.exe"
    if not renpy_exe.exists():
        return {"ran": False, "ok": False, "summary": "SDK 不可用，跳过验证",
                "output": "", "suspects": []}
    # 审核补修：lint 以 SDK 目录为工作目录执行，相对路径会被当成
    # SDK 下的路径找不到工程，导致 lint 空转还报“通过”假象；
    # 一律转绝对路径再传入
    project_dir = str(Path(project_dir).resolve())
    try:
        proc = run_quiet([str(renpy_exe), project_dir, "lint"],
                         capture_output=True, timeout=timeout, cwd=sdk)
    except subprocess.TimeoutExpired:
        return {"ran": True, "ok": False, "summary": "lint 超时",
                "output": "", "suspects": []}
    except Exception as e:
        return {"ran": True, "ok": False, "summary": f"lint 无法执行：{e}",
                "output": "", "suspects": []}

    out = _decode_pipe(proc.stdout or b"")
    err = _decode_pipe(proc.stderr or b"")
    text = out + ("\n" + err if err.strip() else "")

    suspects = []
    for line in text.splitlines():
        if _ERR_LINE_RE.search(line):
            suspects.append(line.strip())

    ok = proc.returncode == 0 and not suspects
    summary = "通过，未发现错误" if ok else \
        (f"发现 {len(suspects)} 处疑似错误" if suspects
         else f"lint 退出码 {proc.returncode}")
    return {"ran": True, "ok": ok, "summary": summary,
            "suspects": suspects[:20], "output": text[-6000:]}
