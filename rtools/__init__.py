"""RenPySlim 核心引擎。

不依赖 Web/CLI 层，可独立调用与测试。
模块职责与依赖方向见 docs/ARCHITECTURE.md；需求与待办见 docs/BACKLOG.md。

Copyright (C) 2026  RenPySlim contributors
SPDX-License-Identifier: AGPL-3.0-or-later
本包按 AGPL-3.0 发布，详见仓库根目录 LICENSE。
"""

import importlib.util

__version__ = "0.16.0"

# pip 包名 -> 导入名（第三方依赖，与 requirements.txt 保持同步）。
# 本 __init__ 不导入任何第三方包，入口可在重模块加载前安全自检。
_DEP_IMPORT_NAMES = {
    "pillow": "PIL",
    "fonttools": "fontTools",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "py7zr": "py7zr",
    "pystray": "pystray",
}
# CLI 最小依赖（图片/字体/压缩包处理）；GUI 另需 Web 服务与托盘组件。
CORE_DEPS = ("pillow", "fonttools", "py7zr")
GUI_EXTRA_DEPS = ("fastapi", "uvicorn", "pystray")


def missing_dependencies(gui: bool = False) -> list:
    """启动自检：返回尚未安装的 pip 包名列表（空 = 齐全）。

    从源码运行且忘装依赖时，入口据此给人话指引而非裸 traceback；
    exe 发行版依赖已打包，此检查永远返回空。
    """
    names = CORE_DEPS + (GUI_EXTRA_DEPS if gui else ())
    return [p for p in names
            if importlib.util.find_spec(_DEP_IMPORT_NAMES[p]) is None]
