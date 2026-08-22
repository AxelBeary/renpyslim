"""脚本反编译（实验性）：把编译产物 rpyc 还原成可编辑的 rpy。

内嵌开源工具 unrpyc v2.x（MIT 许可，源码与 LICENSE 见
rtools/vendor/unrpyc/；署名见 THIRD_PARTY_NOTICES.md）。

用途：成品模式没有源码时，引用被焊死在编译脚本里，图片/音频
不能换格式。反编译把"源码"找回来后，既有的引用改写与格式转换
机制全部解锁；Ren'Py 引擎下次启动发现 rpy 比 rpyc 新会自动重编译，
玩家无感。

安全原则：只在工作副本上反编译，绝不碰原件；反编译失败的文件
告警跳过（对应资源自动退回"同名压缩"保守策略，不会坏）。
"""
from __future__ import annotations

import sys
from pathlib import Path

from .models import SKIP_DIRS


# vendored unrpyc 要求 unrpyc.py 与 decompiler/ 包在同一个
# sys.path 目录下（其内部是顶层 import decompiler）
def _vendor_root() -> Path:
    # exe 形态：PyInstaller 把 datas 解到临时目录 sys._MEIPASS
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "rtools" / "vendor" / "unrpyc"
    return Path(__file__).resolve().parent / "vendor" / "unrpyc"


_VENDOR_DIR = str(_vendor_root())


def _import_unrpyc():
    if _VENDOR_DIR not in sys.path:
        sys.path.insert(0, _VENDOR_DIR)
    import unrpyc  # vendored 上游代码，非 PyPI 依赖
    return unrpyc


class DecompileError(Exception):
    pass


def decompile_scripts(root: str, progress=None,
                      try_harder: bool = False) -> dict:
    """把 root 下全部 .rpyc/.rpymc 原地反编译为 .rpy/.rpym。

    返回 {"decompiled": n, "skipped": n, "failed": [相对路径...]}
    - 旁边已有同名 rpy/rpym（成品带着源码发布）：跳过不覆盖，
      真实源码永远比反编译产物可信
    - 反编译失败（混淆/未知语法）：记入 failed，由调用方告警；
      这些脚本对应的资源因查不到引用会自动退回保守策略
    """
    unrpyc = _import_unrpyc()
    root_p = Path(root)
    files = []
    for p in sorted(root_p.rglob("*")):
        # 跳过 saves/cache 等目录：其中的 rpyc 是运行残留，不是游戏脚本；
        # 反编译它们会污染目录且干扰引用分析（SKIP_DIRS 见 models）。
        # 收口修复：必须用相对 root 的路径比对——p 是绝对路径，
        # 项目位于 C:\Users\cache\... 这类目录时旧写法会把全部脚本误跳过。
        try:
            rel_parts = p.relative_to(root_p).parts
        except ValueError:
            rel_parts = p.parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        if p.suffix.lower() not in (".rpyc", ".rpymc"):
            continue
        # 排除上一轮残留的半成品（形如 x.rpy.rpyc），防止反编译自己的失败产物；
        # 新产生的半成品会在失败时即时删除（见下方 except 分支）
        stem_lower = p.name[: -len(p.suffix)].lower()
        if stem_lower.endswith((".rpy", ".rpym")):
            continue
        files.append(p)
    stats = {"decompiled": 0, "skipped": 0, "failed": []}
    total = len(files)
    for i, f in enumerate(files, start=1):
        out = f.with_suffix(".rpy" if f.suffix.lower() == ".rpyc" else ".rpym")
        rel = f.relative_to(root_p).as_posix()
        if progress:
            progress(i, total, rel)
        if out.exists():
            stats["skipped"] += 1
            continue
        ctx = unrpyc.Context()
        try:
            unrpyc.decompile_rpyc(f, ctx, overwrite=False,
                                  try_harder=try_harder)
        except Exception:
            ctx.state = "error"
            # 半成品 rpy 比没有 rpy 更危险：会被流水线误认为源码，
            # 且重跑时因文件已存在被跳过、永不重试。失败即删除，
            # 下次 out.exists() 为 False 会自动重新反编译。
            out.unlink(missing_ok=True)
        if ctx.state == "ok" and out.exists():
            stats["decompiled"] += 1
        else:
            stats["failed"].append(rel)
    return stats
