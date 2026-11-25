"""图形界面入口：启动本地服务并自动打开浏览器。

双击 main.py（或打包后的 exe）即可使用；
也支持把一个 zip / 7z / rar / 文件夹直接拖到工具图标上：
系统会把路径作为参数传进来，工具自动填进页面并开始分析。

运行期间系统托盘（右下角）常驻图标：右键可打开界面或退出工具。
无头模式请用 cli.py。

Copyright (C) 2026  RenPySlim contributors
SPDX-License-Identifier: AGPL-3.0-or-later
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, version 3. This program is
distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; see the LICENSE file for details.
"""
from __future__ import annotations

import socket
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

# 让打包后的 exe 也能找到 rtools / web
BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
sys.path.insert(0, str(BASE))

# 窗口模式（无控制台）下 stdout/stderr 是 None，任何 print 都会崩，先接到黑洞
import os
if sys.stdout is None or sys.stderr is None:
    _devnull = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stdout or _devnull
    sys.stderr = sys.stderr or _devnull

# Windows 的管道/重定向默认按 ANSI 代码页编码（CI runner 上是 cp1252），中文会被
# backslashreplace 成反斜杠-u 转义——画面上看着"没坏"，但机器读到的不是原字符。
# 与 cli.py 同口径强制 UTF-8：脚本与 agent 拿到的 stdout/stderr 一律是 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

import rtools

# 入口参数分流：本文件既是图形界面入口，也是打包后 exe 的唯一入口。
# 窗口版 exe 没有自己的控制台，命令行用户/自动化脚本敲 --version、
# --help 或 cli 子命令时，必须先分流到无头 CLI，否则会被当成"要打开的工程路径"。
CLI_SUBCOMMANDS = frozenset({
    "env", "analyze", "optimize", "full", "slimapk", "slimfont", "history", "package",
})
INFO_FLAGS = frozenset({"--version", "-V", "--help", "-h"})

# 未知选项的退出码（与 cli.py 的参数错误口径一致：非 0 即失败）
BAD_OPTION_EXIT = 2


def _attach_parent_console() -> None:
    """窗口版 exe 被终端调用时，把标准输出接回父控制台。

    console=False 构建的进程没有自己的控制台，默认什么都打不出来；
    AttachConsole(ATTACH_PARENT_PROCESS) 借用调用方终端，这样
    `RenPySlim.exe --version` 在 cmd / PowerShell 里看得见输出。
    双击启动时没有父控制台，此函数安静失败，不影响界面流程。
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        if not ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            return
        # 不写死 encoding：真实控制台按自己的代码页解码，硬写 UTF-8 反而会花屏。
        sys.stdout = open("CONOUT$", "w", errors="replace", buffering=1)
        sys.stderr = open("CONOUT$", "w", errors="replace", buffering=1)
    except Exception:
        pass


def _classify_argv(argv) -> str:
    """把入口参数分成三类：cli（走无头 CLI）/ gui（开界面）/ bad（未知选项）。

    以 "-" 开头的参数一律不当路径：这正是 v0.17.2 之前把 --version 填进
    工程路径框的原因。已知子命令与信息开关交给 cli.py，口径与 AGENTS.md 一致。
    """
    if not argv:
        return "gui"
    first = argv[0]
    if first in INFO_FLAGS or first in CLI_SUBCOMMANDS:
        return "cli"
    if first.startswith("-"):
        return "bad"
    return "gui"


def _ensure_dependencies() -> None:
    """启动自检：从源码运行且忘装依赖时，给人话指引而非裸 traceback。
    窗口模式没有控制台可看，额外弹一个系统对话框。"""
    missing = rtools.missing_dependencies(gui=True)
    if not missing:
        return
    msg = (f"缺少 Python 依赖：{', '.join(missing)}\n\n"
           "请在仓库根目录先运行：\n"
           "    pip install -r requirements.txt\n"
           "然后重新启动本工具。\n"
           "（exe 发行版用户不会遇到此问题，依赖已随包内置）")
    try:
        print(msg, file=sys.stderr)
    except Exception:
        pass
    try:
        import tkinter
        from tkinter import messagebox
        _root = tkinter.Tk()
        _root.withdraw()
        messagebox.showerror("RenPySlim — 缺少依赖 / Missing dependencies", msg)
        _root.destroy()
    except Exception:
        pass
    sys.exit(1)

from rtools import runtime  # noqa: E402

DEFAULT_PORT = 52786
HOST = "127.0.0.1"


def _valid_port(port: object) -> bool:
    """端口号必须是 1..65535 的整数；0 只用于让系统临时分配端口。"""
    return (isinstance(port, int) and not isinstance(port, bool)
            and 1 <= port <= 65535)


def _port_free(port: int) -> bool:
    if not _valid_port(port):
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, port))
            return True
        except OSError:
            return False


def _port_alive(port: int) -> bool | None:
    """探测已有实例；非法端口号返回 None，避免交给 socket 后抛 OverflowError。"""
    if not _valid_port(port):
        return None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect((HOST, port))
            return True
        except OSError:
            return False


def _pick_port() -> int:
    try:
        preferred = int(os.environ.get("RENPYTOOLS_PORT", "") or DEFAULT_PORT)
    except ValueError:
        preferred = DEFAULT_PORT
    if not _valid_port(preferred):
        preferred = DEFAULT_PORT
    if _port_free(preferred):
        return preferred
    # 首选被占：让系统分配一个空闲端口，绝不因端口问题启动失败
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _running_instance_port() -> int | None:
    """已有实例在跑的话，返回它的端口。"""
    port = runtime.read_port()
    return port if port and _port_alive(port) else None


def _make_icon_image():
    """托盘图标：优先用 assets 里的小狗图，找不到时画个备用图标。"""
    from PIL import Image, ImageDraw, ImageFont
    bundled = BASE / "assets" / "icon.png"
    if bundled.exists():
        try:
            img = Image.open(bundled).convert("RGBA")
            img.thumbnail((64, 64), Image.LANCZOS)
            return img
        except Exception:
            pass
    # 备用：蓝底圆角方块 + 白色 R
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, 62, 62], radius=14, fill=(91, 140, 255, 255))
    try:
        font = ImageFont.load_default(size=40)
    except TypeError:          # 老版本 Pillow 不支持 size 参数
        font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), "R", font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((64 - w) / 2 - bbox[0], (64 - h) / 2 - bbox[1]), "R",
           font=font, fill=(255, 255, 255, 255))
    return img


def _start_tray(url: str):
    """启动托盘图标（失败不影响主功能，静默跳过）。"""
    try:
        import pystray
    except Exception:
        return None
    try:
        menu = pystray.Menu(
            pystray.MenuItem("打开 RenPySlim",
                             lambda *_: webbrowser.open(url), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出工具",
                             lambda icon, _item: (icon.stop(), runtime.terminate())),
        )
        icon = pystray.Icon("renpyslim", _make_icon_image(),
                            f"RenPySlim 运行中 · {url}", menu)
        threading.Thread(target=icon.run, daemon=True).start()
        return icon
    except Exception:
        return None


def main() -> None:
    argv = sys.argv[1:]
    kind = _classify_argv(argv)

    # 命令行形态：交给无头 CLI（--version / --help / analyze 等子命令）。
    # 先把输出接回调用方终端，窗口版 exe 才看得见结果。
    if kind == "cli":
        _attach_parent_console()
        from cli import main as cli_main
        sys.exit(cli_main(argv))

    if kind == "bad":
        _attach_parent_console()
        print(f"未知选项：{argv[0]}", file=sys.stderr)
        print("用法：RenPySlim.exe [工程目录|压缩包]    或    "
              "RenPySlim.exe --version / --help / <子命令>", file=sys.stderr)
        sys.exit(BAD_OPTION_EXIT)

    # 只有确认要用界面了才做依赖自检：--version 之类不依赖第三方包，
    # 不该在缺依赖的机器上先弹一个缺依赖对话框。
    _ensure_dependencies()

    # 拖拽/命令行传入的路径（系统拖拽会自动加引号并作为参数传入）
    path_arg = argv[0] if argv else None

    # 已有实例在运行：不重复启动，把路径转给老实例（新开一个标签页）
    existing = _running_instance_port()
    if existing:
        webbrowser.open(f"http://{HOST}:{existing}/"
                        + ("?open=" + urllib.parse.quote(path_arg, safe="")
                           if path_arg else ""))
        return

    port = _pick_port()
    runtime.write_port(port)

    url = f"http://{HOST}:{port}/"
    if path_arg:
        url += "?open=" + urllib.parse.quote(path_arg, safe="")

    # 延迟 1 秒等服务器就绪后再开浏览器
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    _start_tray(f"http://{HOST}:{port}/")

    import uvicorn
    from web.app import app  # noqa: F401

    print(f"RenPySlim 已启动：{url}")
    print("退出方式：托盘图标右键→退出工具，或界面里的“退出工具”按钮。")
    try:
        uvicorn.run(app, host=HOST, port=port, log_level="warning")
    finally:
        runtime.clear()


if __name__ == "__main__":
    main()
