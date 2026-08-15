"""无头模式入口：全程 JSON 输出，供脚本 / agent 直接调用。

用法示例：
  python cli.py env
  python cli.py analyze E:\\mygame --mode project
  python cli.py optimize E:\\mygame --mode project --preset balanced
  python cli.py package E:\\mygame --platforms pc,mac
  python cli.py full    E:\\mygame --platforms pc        # 优化+打包一条龙

约定：结果 JSON 走 stdout；过程日志走 stderr，不污染 JSON。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rtools import packager, pipeline, scanner, analyzer, charset, font_tool
from rtools import archives
from rtools import __version__
from rtools.config import OptimizeOptions, CharsetOptions, PRESETS
from rtools.models import Progress


def _log(stage: str, message: str) -> None:
    print(f"[{stage}] {message}", file=sys.stderr, flush=True)


# Windows 控制台默认 GBK，会把中文 JSON 弄坏，强制 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def _ok(data: dict) -> int:
    print(json.dumps({"ok": True, **data}, ensure_ascii=False, indent=2))
    return 0


def _fail(msg: str) -> int:
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False, indent=2))
    return 1


def _build_options(args) -> OptimizeOptions:
    opts = OptimizeOptions()
    opts.preset = getattr(args, "preset", "balanced") or "balanced"
    opts.in_place = getattr(args, "in_place", False)
    opts.delete_unreferenced = getattr(args, "delete_unreferenced", False)
    opts.quarantine_unused = getattr(args, "quarantine_unused", False)
    opts.png_quant = getattr(args, "png_quant", False)
    opts.experimental_remap = getattr(args, "remap", False)
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
            return _fail(str(e))

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
        else:
            # 分析必须只读：解包用临时目录，用完立即清理
            import shutil
            import tempfile
            extract = Path(tempfile.mkdtemp(prefix="rtools_analyze_"))
            try:
                loose = scanner.scan_assets(str(path), progress=scan_log)
                packed = scanner.scan_rpa_assets(str(path), str(extract),
                                                 progress=scan_log)
                report = analyzer.analyze(loose + packed, str(path), "dist")
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
    if args.mode:
        mode = args.mode
    elif is_arc:
        mode = "dist"
    else:
        game = path / "game"
        mode = "project" if (game.is_dir() and any(game.rglob("*.rpy"))) else "dist"
    opts = _build_options(args)
    work_root = args.work_root or str(path.parent / "_rtools_work")
    output_dir = args.output or str(path.parent / "_rtools_output")
    Path(work_root).mkdir(parents=True, exist_ok=True)
    progress = Progress(_log)
    try:
        if mode == "project":
            result = pipeline.run_project(str(path), opts, work_root,
                                          output_dir, progress)
        else:
            # run_dist_smart 兼容目录与压缩包输入，压缩包会自动回包
            result = pipeline.run_dist_smart(str(path), opts, work_root,
                                             output_dir, progress,
                                             password=args.password)
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
    except FileNotFoundError as e:
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
    try:
        opt_result = pipeline.run_project(str(path), opts, work_root,
                                          output_dir, progress)
    except pipeline.PipelineError as e:
        return _fail(str(e))

    sdk = packager.find_sdk(args.sdk)
    if not sdk:
        return _fail("优化完成，但找不到 Ren'Py SDK，无法打包。请用 --sdk 指定。")
    platforms = [p.strip() for p in (args.platforms or "pc").split(",") if p.strip()]
    pkg_result = packager.package_project(sdk, opt_result["working_dir"],
                                          platforms, args.destination,
                                          log=_log_wrapper,
                                          archive_rpa=args.archive_rpa)
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
    ap = argparse.ArgumentParser(prog="renpyslim", description="RenPySlim：Ren'Py 资源瘦身与打包工具箱（无头模式）")
    ap.add_argument("--version", action="version", version=f"renpyslim {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("env", help="环境体检（SDK/FFmpeg/Java）")
    p.add_argument("--sdk", default=None)
    p.set_defaults(func=cmd_env)

    p = sub.add_parser("analyze", help="扫描分析资源")
    p.add_argument("path")
    p.add_argument("--mode", choices=["project", "dist"], default=None)
    p.add_argument("--work-root", default=None)
    p.add_argument("--password", default=None, help="压缩包密码（如有）")
    p.set_defaults(func=cmd_analyze)

    for name, func, help_ in (("optimize", cmd_optimize, "优化资源"),
                              ("full", cmd_full, "优化+打包一条龙")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("path")
        p.add_argument("--mode", choices=["project", "dist"], default=None)
        p.add_argument("--preset", choices=list(PRESETS), default="balanced")
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
        p.add_argument("--remap", action="store_true",
                       help="实验性：成品注入运行时重映射脚本（无源码也能转 WebP）")
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
    p.add_argument("--preset", choices=list(PRESETS), default="balanced")
    p.add_argument("--sdk", default=None, help="Ren'Py SDK 路径（用于找签名工具）")
    p.add_argument("--keystore", default=None, help="签名 keystore 文件")
    p.add_argument("--ks-pass", default=None, help="keystore 密码")
    p.add_argument("--key-alias", default=None, help="密钥别名（可选）")
    p.add_argument("--key-pass", default=None, help="密钥密码（可选，默认同 keystore 密码）")
    p.add_argument("--gen-key", action="store_true",
                   help="自动生成新钥匙签名（不需要任何现有密码；新身份，玩家需卸载重装）")
    p.add_argument("--key-password", default=None, help="配 --gen-key：自定义新钥匙密码（默认自动随机）")
    p.add_argument("--extra-chars", default="", help="字体保底手动追加字符")
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
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
