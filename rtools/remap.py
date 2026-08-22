"""运行时文件重映射（实验性，BACKLOG B9）。

场景：成品没有 .rpy 源码，引用改写无从下手，但文件名的"请求"
都经过引擎的加载管线。往 game/ 注入一个极小的一次性脚本，
在 config.file_open_callback / config.loadable_callback 里把
"旧名字的请求"透明指到"新文件"，即可在不碰编译脚本的前提下
完成格式转换（如 PNG→WebP）。

安全约束：
- 只在用户显式开启实验选项时注入；
- 脚本只查表转发，表外请求一律返回 None（引擎走原流程），零侵入；
- 新文件名不进入映射表键，杜绝回调自递归；
- 脚本语法保持 Ren'Py 7/8 双兼容（py2/py3）。
钩子依据：Ren'Py 官方 loader.py 的 file_open_callbacks 链与
loadable() 对 config.loadable_callback 的短路判断（8.5 已核实）。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

REMAP_SCRIPT_NAME = "rtools_remap.rpy"

# 钩子最低引擎版本：config.file_open_callback / loadable_callback
# 链在 8.0 之前行为不一致，注入可能导致游戏加载错乱（审核修复）
_MIN_REMAP_VERSION = (8, 0, 0)

_TEMPLATE = '''# 由 RenPySlim 自动生成：运行时文件重映射（实验性功能）。
# 删掉本文件即可完全还原。请勿手动编辑。
init -999 python:
    _renpyslim_remap = {mapping}

    def _renpyslim_remap_open(name):
        _key = name.replace("\\\\", "/").lower()
        _new = _renpyslim_remap.get(_key)
        if _new is None:
            return None
        return renpy.loader.load_core(_new)

    def _renpyslim_remap_loadable(name):
        _key = name.replace("\\\\", "/").lower()
        _new = _renpyslim_remap.get(_key)
        if _new is None:
            return None
        return renpy.loader.loadable_core(_new)

    config.file_open_callback = _renpyslim_remap_open
    config.loadable_callback = _renpyslim_remap_loadable
'''


def build_remap_script(mapping: dict[str, str]) -> str:
    """生成重映射脚本文本。mapping: 旧路径 -> 新路径（相对 game/）。

    键统一为小写正斜杠形式（引擎加载前会 lowercase 归一）。
    """
    norm = {}
    for old, new in mapping.items():
        key = old.replace("\\", "/").lower()
        norm[key] = new.replace("\\", "/")
    body = json.dumps(norm, ensure_ascii=False, indent=8, sort_keys=True)
    # 缩进对齐到 python 块内
    lines = body.split("\n")
    body = lines[0] + "\n" + "\n".join("    " + ln for ln in lines[1:])
    return _TEMPLATE.format(mapping=body)


def parse_remap_targets(script_text: str) -> int:
    """数一数脚本里映射了多少条（供报告/测试用）。"""
    return script_text.count('": ')


def check_remap_support(game_root: Path) -> tuple[bool, str]:
    """注入前预检：该游戏能不能安全启用运行时重映射（审核修复）。

    两项检查：
    1. script_version.txt 解析出的引擎版本必须 >= 8.0.0（钩子链的
       行为边界）；文件缺失或格式坏一律拒绝注入。
    2. 扫描 .rpy 源码（排除本工具注入的脚本）：游戏自己设置了
       config.file_open_callback / config.loadable_callback 时，
       注入会直接覆盖游戏回调，拒绝注入。
    返回 (支持, 人类可读原因)；支持时原因恒为空串。
    """
    game_root = Path(game_root)
    sv_path = game_root / "script_version.txt"
    if not sv_path.exists():
        return False, "缺少 script_version.txt，无法确认引擎版本，已拒绝注入 remap"
    try:
        raw = sv_path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return False, "读取 script_version.txt 失败，已拒绝注入 remap"
    try:
        parsed = ast.literal_eval(raw)
        version = tuple(int(x) for x in parsed)
    except Exception:
        return False, f"无法解析 script_version.txt 内容（{raw!r}），已拒绝注入 remap"
    if version < _MIN_REMAP_VERSION:
        v_show = ".".join(str(x) for x in version)
        return False, f"引擎版本 {v_show} 低于 remap 要求的 8.0.0，已拒绝注入"

    # 回调冲突扫描：只看 .rpy 明文脚本，跳过本工具自己注入的脚本
    for p in game_root.rglob("*.rpy"):
        if not p.is_file() or p.name == REMAP_SCRIPT_NAME:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "file_open_callback" in text or "loadable_callback" in text:
            return False, "检测到游戏自身设置了文件加载回调，remap 可能冲突"
    return True, ""


def parse_remap_mapping(script_text: str) -> tuple[dict[str, str], bool]:
    """从已注入的重映射脚本里把映射表读回来。

    二次运行时用来合并旧映射：只拿新表覆盖写会把旧条目弄丢，
    而旧表里的原文件已被删除，丢了映射 = 图加载不到。（审核修复）
    返回 (映射, 是否解析成功)：任何失败（缺标记/无花括号/JSON 错/
    非 dict）返回 ({}, False)，调用方据此决定是信任旧表还是报警。
    """
    marker = "_renpyslim_remap = "
    idx = script_text.find(marker)
    if idx < 0:
        return {}, False
    brace = script_text.find("{", idx)
    if brace < 0:
        return {}, False
    try:
        obj, _end = json.JSONDecoder().raw_decode(script_text[brace:])
    except ValueError:
        return {}, False
    if not isinstance(obj, dict):
        return {}, False
    return {str(k): str(v) for k, v in obj.items()}, True
