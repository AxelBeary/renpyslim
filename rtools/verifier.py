"""自动验证：优化完成后调用官方 SDK 的 lint 做静态检查。

lint 能查出引用断裂、图片缺失、脚本错误等问题，是"优化后仍能运行"
的第一道官方背书。启动级验证更彻底但耗时，保留给用户手动确认。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .procutil import run_quiet


def lint_project(sdk: str, project_dir: str, timeout: int = 600) -> dict:
    """对工程跑 renpy lint。返回 {ran, ok, summary, output}。

    ok 判定：进程正常退出，且输出里没有疑似错误行。
    lint 的统计段落（字数、菜单数等）永远以空行分隔在末尾，
    错误行形如 "文件.rpy:12: 描述"，用"包含冒号且含 error/错误"粗筛。
    """
    renpy_exe = Path(sdk) / "renpy.exe"
    if not renpy_exe.exists():
        return {"ran": False, "ok": False, "summary": "SDK 不可用，跳过验证",
                "output": ""}
    # 审核补修：lint 以 SDK 目录为工作目录执行，相对路径会被当成
    # SDK 下的路径找不到工程，导致 lint 空转还报“通过”假象；
    # 一律转绝对路径再传入
    project_dir = str(Path(project_dir).resolve())
    try:
        proc = run_quiet([str(renpy_exe), project_dir, "lint"],
                         capture_output=True, timeout=timeout, cwd=sdk)
    except subprocess.TimeoutExpired:
        return {"ran": True, "ok": False, "summary": "lint 超时", "output": ""}
    except Exception as e:
        return {"ran": True, "ok": False, "summary": f"lint 无法执行：{e}",
                "output": ""}

    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    text = out + ("\n" + err if err.strip() else "")

    suspects = []
    for line in text.splitlines():
        low = line.lower()
        # 错误行特征：带 .rpy 位置前缀，或明确写了 error；
        # 排除 lint 末尾的正常统计与提示语
        if ("error" in low or "错误" in line) and ".rpy" in low:
            suspects.append(line.strip())

    ok = proc.returncode == 0 and not suspects
    summary = "通过，未发现错误" if ok else \
        (f"发现 {len(suspects)} 处疑似错误" if suspects
         else f"lint 退出码 {proc.returncode}")
    return {"ran": True, "ok": ok, "summary": summary,
            "suspects": suspects[:20], "output": text[-6000:]}
