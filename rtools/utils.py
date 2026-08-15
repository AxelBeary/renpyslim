"""通用小工具：各处共用的小函数归一到这里，避免复制粘贴。"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional


def fmt_size(size: Optional[int]) -> str:
    """把字节数格式成人话体积。None 显示为 —。"""
    if size is None:
        return "—"
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    return f"{size / 1024:.0f} KB"


def safe_join(base: Path, member: str) -> Optional[Path]:
    """把压缩包/封包里的条目名安全地拼到 base 下（防 zip-slip）。

    条目名来自不可信输入：含 .. 、绝对路径或反斜杠穿越的
    一律拒绝（返回 None），由调用方跳过并告警。
    """
    norm = member.replace("\\", "/")
    parts = [seg for seg in norm.split("/") if seg and seg != "."]
    # .. 穿越、盘符（C: 之类，合法文件名里不会出现冒号）一律拒绝
    if not parts or any(seg == ".." or ":" in seg for seg in parts):
        return None
    out = base.joinpath(*parts)
    try:
        out.relative_to(base)
    except ValueError:
        return None
    return out


def find_suffix_clashes(rels: Iterable[str], new_ext: str) -> set[str]:
    """找出换后缀转换会撞车的目标：多个源文件换后同名。

    例如 foo.png 和 foo.jpg 都要转 foo.webp——并行转换会互覆，
    重映射表也会两个键指同一目标。返回撞车的目标相对路径集合，
    调用方对这些目标降级为同名压缩。
    """
    seen: dict[str, int] = {}
    for rel in rels:
        target = Path(rel).with_suffix(new_ext).as_posix()
        seen[target] = seen.get(target, 0) + 1
    return {t for t, n in seen.items() if n > 1}
