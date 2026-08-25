"""无头模式入口：全程 JSON 输出，供脚本 / agent 直接调用。

用法示例：
  python cli.py env
  python cli.py analyze E:\\mygame --mode project
  python cli.py optimize E:\\mygame --mode project --preset balanced
  python cli.py package E:\\mygame --platforms pc,mac
  python cli.py full    E:\\mygame --platforms pc        # 优化+打包一条龙

约定：结果 JSON 走 stdout；过程日志走 stderr，不污染 JSON。

Copyright (C) 2026  RenPySlim contributors
SPDX-License-Identifier: AGPL-3.0-or-later
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, version 3. This program is
distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; see the LICENSE file for details.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

import rtools

# Windows 控制台默认 GBK，会把中文 JSON 弄坏，强制 UTF-8
# （提到自检之前，缺依赖的中文指引同样要可读）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# 启动自检：从源码运行且忘装依赖时，输出人话指引而非裸 traceback；
# 同样守 JSON 契约（结果走 stdout、顶层有 ok、退出码 1）。
_missing = rtools.missing_dependencies(gui=False)
if _missing:
    print(json.dumps(
        {"ok": False,
         "error": f"缺少 Python 依赖：{', '.join(_missing)}。"
                  "请先在仓库根目录运行 'pip install -r requirements.txt' 后重试"
                  "（exe 发行版用户不会遇到此问题，依赖已随包内置）。"},
        ensure_ascii=False, indent=2))
    sys.exit(1)

from rtools import packager, pipeline, scanner, analyzer, charset, font_tool  # noqa: E402
from rtools import archives  # noqa: E402
from rtools import __version__  # noqa: E402
from rtools.config import (OptimizeOptions, CharsetOptions, PRESETS,  # noqa: E402
                           DEFAULT_PRESET)
from rtools.models import Progress  # noqa: E402


def _log(stage: str, message: str) -> None:
    print(f"[{stage}] {message}", file=sys.stderr, flush=True)


def _ok(data: dict) -> int:
    print(json.dumps({"ok": True, **data}, ensure_ascii=False, indent=2))
    return 0


def _fail(msg: str) -> int:
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False, indent=2))
    return 1





class _JsonArgumentParser(argparse.ArgumentParser):
    """参数错误也守 JSON 契约（审核修复）。

    argparse 默认把 usage 打到 stderr 后退出 2，stdout 无 JSON，
    违反"结果 JSON 走 stdout 且顶层有 ok"的约定。非法子命令、
    缺必选参数（含 required 子命令缺失）、非法取值统统改走这里：
    stdout 输出 {"ok": false, "error": ..., "usage": ...} 后退出 1。
    """

    def error(self, message: str):  # type: ignore[override]
        print(json.dumps({"ok": False, "error": message,
                          "usage": self.format_usage().strip()},
                         ensure_ascii=False), flush=True)
        sys.exit(1)


def _make_cancel():
    """把 Ctrl+C 映射为取消回调（审核修复 中-3）。

    不传 cancel 时 SIGINT 既不触发 futures.cancel 也不落部分清单，
    还要等正在跑的长任务自然结束；现在按取消路径干净收尾。
    """
    flag = {"v": False}

    def _handler(sig, frame):
        if not flag["v"]:
            _log("cancel", "收到取消请求，正在停下并保存已完成部分的清单……")
        flag["v"] = True

    try:
        signal.signal(signal.SIGINT, _handler)
    except ValueError:
        pass   # 非主线程（测试场景）注册不了，忽略
    return lambda: flag["v"]


def _build_options(args) -> OptimizeOptions:
    opts = OptimizeOptions()
    opts.preset = getattr(args, "preset", None) or DEFAULT_PRESET
    opts.in_place = getattr(args, "in_place", False)
    opts.delete_unreferenced = getattr(args, "delete_unreferenced", False)
    opts.quarantine_unused = getattr(args, "quarantine_unused", False)
    opts.png_quant = getattr(args, "png_quant", False)
    opts.experimental_remap = getattr(args, "remap", False)
    opts.experimental_av1 = getattr(args, "av1", False)
    opts.experimental_decompile = getattr(args, "decompile", False)
    opts.do_videos = getattr(args, "videos", False)
    if getattr(args, "no_cache", False):
        opts.use_cache = False
    cs = CharsetOptions()
    cs.extra_chars = getattr(args, "extra_chars", "") or ""
    cs.fullwidth = getattr(args, "fullwidth", False)
    cs.kana = getattr(args, "kana", False)
    opts.charset = cs
    return opts


def cmd_env(args) -> int:
    return _ok({"environment": packager.check_environment(args.sdk)})


def cmd_analyze(args) -> int:
    path = Path(args.path)
    if not path.exists():
        return _fail(f"路径不存在：{args.path}")

    # 压缩包直接进：解压到临时目录，分析完清理
    cleanup_dir = None
    if archives.is_archive(str(path)):
        import tempfile
        cleanup_dir = tempfile.mkdtemp(prefix="rtools_unpack_")
        try:
            archives.extract_archive(str(path), cleanup_dir, args.password)
            path = Path(archives.find_dist_root(cleanup_dir))
        except archives.ArchiveError as e:
            # 审核修复（中-32）：解压失败路径以前直接 return，
            # 泄漏临时目录（外层 try/finally 尚未进入）
            import shutil
            shutil.rmtree(cleanup_dir, ignore_errors=True)
            cleanup_dir = None
            return _fail(str(e))
        except Exception:
            import shutil
            shutil.rmtree(cleanup_dir, ignore_errors=True)
            cleanup_dir = None
            raise

    try:
        # 工程有 .rpy 源码，成品只有 .rpyc，以此自动区分
        mode = args.mode
        if not mode:
            game = path / "game"
            mode = "project" if (game.is_dir()
                                 and any(game.rglob("*.rpy"))) else "dist"
        progress = Progress(_log)
        progress.emit("analyze", f"扫描 {args.path}（{mode} 模式）")
        scan_log = lambda i, t, n: _log("scan", f"扫描资源 {i}/{t}：{n}")
        if mode == "project":
            game = path / "game"
            assets = scanner.scan_assets(str(game), progress=scan_log)
            report = analyzer.analyze(assets, str(game), "project")
            chars, warns = charset.extract_charset(str(game), CharsetOptions())
            report.warnings.extend(warns)
            report.charset_size = len(chars)
            report.languages = charset.detect_languages(str(game))
            # 字体使用处数：让用户在优化前就看到哪些字体只用在一两处
            from rtools.refs import RefIndex
            from rtools import cleanup as _cleanup
            from rtools.models import AssetKind
            ref_index = RefIndex(str(game))
            fonts = [a for a in assets if a.kind == AssetKind.FONT
                     and a.ext in (".ttf", ".otf")]
            report.font_usage, usage_warns = _cleanup.font_usage_report(
                ref_index, fonts)
            report.warnings.extend(usage_warns)
        else:
            # 分析必须只读：解包用临时目录，用完立即清理
            import shutil
            import tempfile
            extract = Path(tempfile.mkdtemp(prefix="rtools_analyze_"))
            try:
                loose = scanner.scan_assets(str(path), progress=scan_log)
                # 审核修复（中-25）：与优化执行路径对齐传 extract_scripts，
                # 避免分析口径与执行口径不一致（潜伏地雷）
                packed = scanner.scan_rpa_assets(str(path), str(extract),
                                                 progress=scan_log,
                                                 extract_scripts=True)
                report = analyzer.analyze(loose + packed, str(path), "dist")
                report.languages = charset.detect_languages(str(path))
            finally:
                shutil.rmtree(extract, ignore_errors=True)
        return _ok({"report": report.to_dict()})
    finally:
        if cleanup_dir:
            import shutil
            shutil.rmtree(cleanup_dir, ignore_errors=True)


def cmd_optimize(args) -> int:
    path = Path(args.path)
    if not path.exists():
        return _fail(f"路径不存在：{args.path}")
    is_arc = archives.is_archive(str(path))
    # 审核修复：--mode 默认 None（用户未传）。旧版用 object() 哨兵当默认值，
    # 哨兵是真值导致 `elif mode_arg:` 恒真，目录输入 100% 报取值校验错。
    mode_arg = getattr(args, "mode", None)
    if is_arc:
        # 压缩包一律按 dist 处理：包内没有源码可当作工程优化；
        # 用户显式传了 --mode project 时打警告但仍走 dist（审核修复）。
        if mode_arg == "project":
            print("警告：压缩包输入不支持工程模式，已自动改用 dist 模式。",
                  file=sys.stderr, flush=True)
        mode = "dist"
    elif mode_arg:
        if mode_arg not in ("project", "dist"):
            return _fail(f"--mode 取值只能是 project 或 dist，收到：{mode_arg}")
        mode = mode_arg
    else:
        game = path / "game"
        mode = "project" if (game.is_dir() and any(game.rglob("*.rpy"))) else "dist"
    opts = _build_options(args)
    work_root = args.work_root or str(path.parent / "_rtools_work")
    output_dir = args.output or str(path.parent / "_rtools_output")
    Path(work_root).mkdir(parents=True, exist_ok=True)
    progress = Progress(_log)
    cancel = _make_cancel()   # 审核修复（中-3）：Ctrl+C 走取消路径
    try:
        if mode == "project":
            result = pipeline.run_project(str(path), opts, work_root,
                                          output_dir, progress, cancel=cancel)
        else:
            # run_dist_smart 兼容目录与压缩包输入，压缩包会自动回包
            result = pipeline.run_dist_smart(str(path), opts, work_root,
                                             output_dir, progress,
                                             password=args.password,
                                             cancel=cancel)
    except (pipeline.PipelineError, archives.ArchiveError) as e:
        return _fail(str(e))
    result.pop("report_dict", None)
    result["report"] = str(result["report"])
    result["changelog"] = str(result["changelog"])
    return _ok({"result": result})


def cmd_package(args) -> int:
    sdk = packager.find_sdk(args.sdk)
    if not sdk:
        return _fail("找不到 Ren'Py SDK，请用 --sdk 指定 SDK 目录。")
    platforms = [p.strip() for p in (args.platforms or "pc").split(",") if p.strip()]
    try:
        result = packager.package_project(sdk, args.path, platforms,
                                          args.destination, log=_log_wrapper,
                                          archive_rpa=args.archive_rpa)
    except Exception as e:
        # 审核修复（中-32）：不只捕 FileNotFoundError，权限/超时等
        # 同样要以 JSON 出口收场
        return _fail(str(e))
    return _ok({"sdk": sdk, "result": result})


def _log_wrapper(msg: str) -> None:
    _log("package", msg)


def cmd_full(args) -> int:
    """模式 A 完整流程：工程优化 -> 官方打包。"""
    path = Path(args.path)
    if not (path / "game").is_dir():
        return _fail(f"{args.path} 不是有效的 Ren'Py 工程（缺少 game 目录）")
    opts = _build_options(args)
    work_root = args.work_root or str(path.parent / "_rtools_work")
    output_dir = args.output or str(path.parent / "_rtools_output")
    Path(work_root).mkdir(parents=True, exist_ok=True)
    progress = Progress(_log)
    cancel = _make_cancel()   # 审核修复（中-3）：Ctrl+C 走取消路径
    try:
        opt_result = pipeline.run_project(str(path), opts, work_root,
                                          output_dir, progress, cancel=cancel)
    except pipeline.PipelineError as e:
        return _fail(str(e))

    sdk = packager.find_sdk(args.sdk)
    if not sdk:
        return _fail("优化完成，但找不到 Ren'Py SDK，无法打包。请用 --sdk 指定。")
    platforms = [p.strip() for p in (args.platforms or "pc").split(",") if p.strip()]
    try:
        # 审核修复（中-32）：打包段以前完全无异常捕获，
        # PermissionError/OSError/TimeoutExpired 裸 traceback 退出，
        # stdout 无 JSON，违反"结果 JSON 走 stdout"契约
        pkg_result = packager.package_project(sdk, opt_result["working_dir"],
                                              platforms, args.destination,
                                              log=_log_wrapper,
                                              archive_rpa=args.archive_rpa)
    except Exception as e:
        return _fail(f"优化已完成但打包失败：{e}")
    opt_result.pop("report_dict", None)
    opt_result["report"] = str(opt_result["report"])
    opt_result["changelog"] = str(opt_result["changelog"])
    return _ok({"optimize": opt_result, "package": pkg_result})


def cmd_slimapk(args) -> int:
    """APK 瘦身（实验性）：同名压缩游戏资源，重打包，可选重签名。"""
    from rtools import apk
    cs = CharsetOptions()
    cs.extra_chars = getattr(args, "extra_chars", "") or ""
    try:
        result = apk.slim_apk(
            args.apk, args.preset, cs,
            sdk=packager.find_sdk(args.sdk),
            keystore=args.keystore, ks_pass=args.ks_pass,
            key_alias=args.key_alias, key_pass=args.key_pass,
            generate_key=args.gen_key,
            new_key_password=args.key_password,
            remap_convert=args.remap,
            progress=Progress(_log))
    except apk.ApkError as e:
        return _fail(str(e))
    return _ok({"result": result})


def cmd_slimfont(args) -> int:
    """独立字体瘦身：选字体 + 文本来源，不依赖游戏工程。"""
    cs = CharsetOptions()
    cs.extra_chars = args.extra_chars or ""
    cs.fullwidth = args.fullwidth
    cs.kana = args.kana
    progress = Progress(_log)
    try:
        result = font_tool.run_font_slim(args.font, args.sources, cs,
                                         output_dir=args.output,
                                         progress=progress)
    except font_tool.FontSlimError as e:
        return _fail(str(e))
    return _ok({"result": result})


def main(argv=None) -> int:
    ap = _JsonArgumentParser(prog="renpyslim", description="RenPySlim：Ren'Py 资源瘦身与打包工具箱（无头模式）")
    ap.add_argument("--version", action="version", version=f"renpyslim {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("env", help="环境体检（SDK/FFmpeg/Java）")
    p.add_argument("--sdk", default=None)
    p.set_defaults(func=cmd_env)

    # 子解析器默认继承主解析器类（_JsonArgumentParser），无需逐个指定。
    p = sub.add_parser("analyze", help="扫描分析资源")
    p.add_argument("path")
    p.add_argument("--mode", choices=["project", "dist"], default=None)
    # 审核修复：--work-root 从未被 analyze 使用，死参数移除。
    p.add_argument("--password", default=None, help="压缩包密码（如有）")
    p.set_defaults(func=cmd_analyze)

    for name, func, help_ in (("optimize", cmd_optimize, "优化资源"),
                              ("full", cmd_full, "优化+打包一条龙")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("path")
        # 审核修复：--mode 只对 optimize 有意义（full 语义上只适用于工程），
        # 从 full 移除；默认 None 表示用户未传，走自动检测。
        if name == "optimize":
            p.add_argument("--mode", choices=["project", "dist"],
                           default=None)
        p.add_argument("--preset", choices=list(PRESETS), default=DEFAULT_PRESET)
        p.add_argument("--work-root", default=None)
        p.add_argument("--output", default=None)
        p.add_argument("--in-place", action="store_true",
                       help="直接修改原件（危险：会自动先备份）")
        p.add_argument("--delete-unreferenced", action="store_true",
                       help="模式B：把疑似无引用文件移入隔离区")
        p.add_argument("--quarantine-unused", action="store_true",
                       help="工程模式：把确认无引用的音频/视频/字体移入隔离区")
        p.add_argument("--png-quant", action="store_true",
                       help="实验性：PNG 有损量化深度压缩（大图再省 60~80%%）")
        p.add_argument("--videos", action="store_true",
                       help="实验性：同名重编码压缩视频")
        p.add_argument("--av1", action="store_true",
                       help="实验性：视频用 AV1 编码（官方支持且更省，仅 Ren'Py 8.0+ 构建的游戏能放）")
        p.add_argument("--remap", action="store_true",
                       help="实验性：成品注入运行时重映射脚本（无源码也能转 WebP）")
        p.add_argument("--decompile", action="store_true",
                       help="实验性：反编译 rpyc 解锁无源码成品的格式转换"
                            "（unrpyc，转换后资源按原样包回封包；处理后请试跑游戏）")
        p.add_argument("--no-cache", action="store_true",
                       help="禁用增量缓存")
        p.add_argument("--extra-chars", default="", help="字体瘦身手动追加字符")
        p.add_argument("--fullwidth", action="store_true", help="保底集加全角符号")
        p.add_argument("--kana", action="store_true", help="保底集加日文假名")
        p.add_argument("--password", default=None, help="压缩包密码（成品瘦身直接输压缩包时用）")
        if name == "full":
            p.add_argument("--platforms", default="pc")
            p.add_argument("--destination", default=None)
            p.add_argument("--sdk", default=None)
            p.add_argument("--archive-rpa", action="store_true",
                           help="打包时把资源封入 main.rpa（官方通道）")
        p.set_defaults(func=func)

    p = sub.add_parser("slimapk", help="APK 瘦身（实验性）")
    p.add_argument("apk", help="APK 文件路径")
    p.add_argument("--preset", choices=list(PRESETS), default=DEFAULT_PRESET)
    p.add_argument("--sdk", default=None, help="Ren'Py SDK 路径（用于找签名工具）")
    p.add_argument("--keystore", default=None, help="签名 keystore 文件")
    p.add_argument("--ks-pass", default=None, help="keystore 密码")
    p.add_argument("--key-alias", default=None, help="密钥别名（可选）")
    p.add_argument("--key-pass", default=None, help="密钥密码（可选，默认同 keystore 密码）")
    p.add_argument("--gen-key", action="store_true",
                   help="自动生成新钥匙签名（不需要任何现有密码；新身份，玩家需卸载重装）")
    p.add_argument("--key-password", default=None, help="配 --gen-key：自定义新钥匙密码（默认自动随机）")
    p.add_argument("--extra-chars", default="", help="字体保底手动追加字符")
    p.add_argument("--remap", action="store_true",
                   help="实验性：图转 WebP、音转 OGG + 注入运行时重映射脚本（收益最大，需 SDK）")
    p.set_defaults(func=cmd_slimapk)

    p = sub.add_parser("slimfont", help="独立字体瘦身（不依赖游戏工程）")
    p.add_argument("font", help="字体文件：ttf/otf/ttc/otc")
    p.add_argument("sources", nargs="*", default=[],
                   help="文本来源：可多个文件或文件夹")
    p.add_argument("--output", default=None, help="输出目录（默认放字体旁边）")
    p.add_argument("--extra-chars", default="", help="手动追加保留的字符")
    p.add_argument("--fullwidth", action="store_true", help="保底集加全角符号")
    p.add_argument("--kana", action="store_true", help="保底集加日文假名")
    p.set_defaults(func=cmd_slimfont)

    p = sub.add_parser("package", help="调用官方 SDK 打包")
    p.add_argument("path")
    p.add_argument("--platforms", default="pc")
    p.add_argument("--destination", default=None)
    p.add_argument("--sdk", default=None)
    p.add_argument("--archive-rpa", action="store_true",
                   help="打包时把资源封入 main.rpa（官方通道）")
    p.set_defaults(func=cmd_package)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:
        # 审核修复（中-32）：统一顶层兜底，任何异常都保证以结果
        # JSON 收场，不再裸 traceback 破坏 stdout 契约
        return _fail(f"意外错误：{type(e).__name__}: {e}")


if __name__ == "__main__":
    sys.exit(main())
