"""崩溃转储（BACKLOG F3）：任务失败时落盘完整堆栈，事后可追溯。

转储目录：~/.renpyslim/crashes/，只写不删，最多保留 20 份旧的自动清掉。
"""
from __future__ import annotations

import time
import traceback
from pathlib import Path

CRASH_DIR = Path.home() / ".renpyslim" / "crashes"
MAX_DUMPS = 20


def write_crash(context: str) -> str:
    """把当前异常的堆栈写入转储文件，返回文件路径。失败静默。"""
    try:
        CRASH_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = CRASH_DIR / f"{stamp}-{context}.txt"
        dest.write_text(traceback.format_exc(), encoding="utf-8")
        _prune()
        return str(dest)
    except OSError:
        return ""


def _prune() -> None:
    try:
        dumps = sorted(CRASH_DIR.glob("*.txt"))
        for old in dumps[:-MAX_DUMPS]:
            old.unlink(missing_ok=True)
    except OSError:
        pass
