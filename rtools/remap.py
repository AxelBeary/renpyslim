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

import json

REMAP_SCRIPT_NAME = "rtools_remap.rpy"

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


def parse_remap_mapping(script_text: str) -> dict[str, str]:
    """从已注入的重映射脚本里把映射表读回来。

    二次运行时用来合并旧映射：只拿新表覆盖写会把旧条目弄丢，
    而旧表里的原文件已被删除，丢了映射 = 图加载不到。（审核修复）
    解析失败返回空 dict（保守：不阻断流程）。
    """
    marker = "_renpyslim_remap = "
    idx = script_text.find(marker)
    if idx < 0:
        return {}
    brace = script_text.find("{", idx)
    if brace < 0:
        return {}
    try:
        obj, _end = json.JSONDecoder().raw_decode(script_text[brace:])
    except ValueError:
        return {}
    if not isinstance(obj, dict):
        return {}
    return {str(k): str(v) for k, v in obj.items()}
