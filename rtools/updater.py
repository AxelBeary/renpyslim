"""自更新检查（BACKLOG F2）：对比 GitHub 最新发布版本。

只做"检查 + 告知"，不自动下载替换——下载由用户点链接完成，简单可靠。
检查失败一律静默（网络不通/限流都不打扰用户）。
"""
from __future__ import annotations

import json
import urllib.request
from typing import Optional

from . import __version__

REPO = "AxelBeary/renpyslim"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASE_PAGE = f"https://github.com/{REPO}/releases"


def _norm(tag: str) -> tuple:
    """'v0.9.0' -> (0, 9, 0)，解析失败返回 (0,)"""
    try:
        return tuple(int(x) for x in tag.lstrip("vV").split("."))
    except ValueError:
        return (0,)


def check_update(timeout: float = 6.0) -> Optional[dict]:
    """返回 {update_available, latest, url}；检查失败返回 None。"""
    try:
        req = urllib.request.Request(API_URL, headers={"User-Agent": "RenPySlim"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latest = str(data.get("tag_name", ""))
        if not latest:
            return None
        return {
            "update_available": _norm(latest) > _norm(__version__),
            "latest": latest,
            "current": __version__,
            "url": data.get("html_url") or RELEASE_PAGE,
        }
    except Exception:
        return None
