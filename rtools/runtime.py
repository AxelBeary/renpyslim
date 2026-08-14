"""进程运行态：端口登记与干净退出。

端口登记文件让"拖文件进已运行的工具"能找到老实例；
退出时统一走 terminate()，保证登记文件被清掉、不留僵尸状态。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

RUNTIME_FILE = Path.home() / ".renpytools" / "runtime.json"


def write_port(port: int) -> None:
    try:
        RUNTIME_FILE.parent.mkdir(parents=True, exist_ok=True)
        RUNTIME_FILE.write_text(json.dumps({"port": port}), encoding="utf-8")
    except OSError:
        pass


def read_port() -> int | None:
    try:
        return int(json.loads(RUNTIME_FILE.read_text(encoding="utf-8"))["port"])
    except Exception:
        return None


def clear() -> None:
    try:
        RUNTIME_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def terminate() -> None:
    """干净退出进程：先清登记文件，再结束自己。

    os._exit 跳过常规清理，但对本工具足够——没有需要刷盘的数据库，
    唯一要紧的就是登记文件，已先行删除。
    """
    clear()
    os._exit(0)
