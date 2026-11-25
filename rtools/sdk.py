"""Ren'Py SDK 启动器解析：Windows 的 renpy.exe 与 Linux/macOS 的 renpy.sh。

背景（v0.18.0 / issue #26）：旧版每个调用点都写死 `Path(sdk) / "renpy.exe"`，
官方 Linux/macOS SDK 只带 `renpy.sh`，于是 lint、packaging、APK remap 在非
Windows 上一律判“SDK 不可用”。把所有“先找启动器再执行”的逻辑收在这里，
调用点只认 `find_launcher()` / `require_launcher()`，不再各写一套。

判定口径：
- 候选顺序固定为 renpy.exe → renpy.sh，与平台无关：同一个 SDK 目录里
  两者只会有其一，顺序保证 Windows 行为零变化。
- renpy.sh 必须可执行（POSIX 的 EACCES 会让 `run_quiet` 直接抛异常，
  这里提前判定，给出“怎么修”的人话提示）。
- 从 Windows 挂载点访问到的 `renpy.sh` 一样可用——只要当前解释器不是 Windows。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# 启动器文件名按优先级排列（同目录一般只存在一个）
LAUNCHER_NAMES = ("renpy.exe", "renpy.sh")

# 提示文案里的“应该装哪个 SDK”：按当前平台给，避免让 Linux 用户去找 .exe
_IS_WINDOWS = sys.platform == "win32"


def _supported_names() -> str:
    """给人看的“本平台应该存在哪个启动器”。"""
    return "renpy.exe" if _IS_WINDOWS else "renpy.sh"


def _is_executable(path: Path) -> bool:
    """只有 shell 启动器（renpy.sh）的执行位有意义。

    renpy.exe 在没有执行位的 POSIX 上本来就跑不起来，那是“执行时”的错误
    （OSError: Permission denied 会带着路径一起抛出来），不该在“解析 SDK”
    这一步就把它判成“没有 SDK”——否则任何只存在 exe 的目录（跨平台备份、
    挂载的 Windows 盘）都会被判无效。
    """
    if _IS_WINDOWS or path.suffix.lower() != ".sh":
        return True
    return os.access(str(path), os.X_OK)


def find_launcher(sdk: Optional[str]) -> Optional[str]:
    """返回 SDK 里的启动器绝对路径；找不到返回 None。

    只要目录里存在 renpy.exe 或 renpy.sh 就算找到——不要求“可执行”，
    因为 Windows 上 .exe 的执行位没有意义；renpy.sh 的可执行检查由
    require_launcher() 做人话提示，避免 discovery 阶段把 SDK 判成不存在。
    """
    if not sdk:
        return None
    base = Path(sdk)
    for name in LAUNCHER_NAMES:
        cand = base / name
        if cand.is_file():
            return str(cand)
    return None


def launcher_error(sdk: Optional[str]) -> str:
    """SDK 不可用时给人看的完整提示。"""
    want = _supported_names()
    where = str(sdk) if sdk else "（未指定 SDK 目录）"
    return f"SDK 里找不到 {want}：{where}"


def require_launcher(sdk: Optional[str]) -> str:
    """返回启动器路径；不存在或不可执行时抛 FileNotFoundError（人话提示）。"""
    launcher = find_launcher(sdk)
    if not launcher:
        raise FileNotFoundError(launcher_error(sdk))
    path = Path(launcher)
    if not _is_executable(path):
        raise FileNotFoundError(
            f"{path.name} 没有执行权限：{path}。"
            f"请运行 chmod +x {path} 后再试。")
    return launcher


def is_sdk_dir(path: Optional[str]) -> bool:
    """目录里是否有 Ren'Py SDK 启动器（发现阶段的判定口径）。

    刻意**不**在这里检查执行位：Windows SDK 的 renpy.exe 在 POSIX 上没有
    执行位也无所谓（那种 SDK 本来就跑不起来，但“有没有 SDK”与“能不能执行”
    是两件事）。执行位留给 require_launcher 在真正要跑的时候判定，
    这样用户得到的提示是“chmod +x 这个文件”，而不是含糊的“找不到 SDK”。
    """
    return find_launcher(path) is not None


def launcher_supports_direct_launch() -> bool:
    """renpy.sh 需要经 shell 解释吗？

    官方 renpy.sh 自带 `#!/bin/sh` 且可执行，`subprocess` 直接以列表形式
    调用即可（不经过 shell，参数里的空格/引号不会拆断）。所以这里恒为
    False——保留成函数是为了让调用点显式表达“不做 shell 拼接”的意图。
    """
    return False
