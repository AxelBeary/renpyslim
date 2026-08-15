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

from rtools import runtime  # noqa: E402

DEFAULT_PORT = 52786
HOST = "127.0.0.1"


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, port))
            return True
        except OSError:
            return False


def _port_alive(port: int) -> bool:
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
    # 拖拽/命令行传入的路径（系统拖拽会自动加引号并作为参数传入）
    path_arg = sys.argv[1] if len(sys.argv) > 1 else None

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
