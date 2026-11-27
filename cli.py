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
import os
import signal
import sys
import threading
import uuid
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
from rtools import archives, crashdump, jobstore, backup  # noqa: E402
from rtools import __version__  # noqa: E402
from rtools.config import (OptimizeOptions, CharsetOptions, PRESETS,  # noqa: E402
                           DEFAULT_PRESET)
from rtools.models import Progress  # noqa: E402


# 中断契约（issue #40）：Ctrl+C 同样以 stdout 结果 JSON 收尾，
# 退出码 130 = 128 + SIGINT，与"参数错误 / 失败 = 1"区分开。
EXIT_INTERRUPTED = 130
_INTERRUPT_ERROR = "用户已中断"
# 取消标志（信号处理器只置位、不抛异常）触发的收尾：历史里如实记取消，
# 不冒充 KeyboardInterrupt（测试仍按"中断 = status error"的既有约定核对）。
_CANCELLED_HISTORY_ERROR = "PipelineCancelled: 用户取消（Ctrl+C）"
_SCAN_CANCELLED_HISTORY_ERROR = "ScanCancelled: 用户取消扫描（Ctrl+C）"


def _log(stage: str, message: str) -> None:
    print(f"[{stage}] {message}", file=sys.stderr, flush=True)


def _ok(data: dict) -> int:
    print(json.dumps({"ok": True, **data}, ensure_ascii=False, indent=2))
    if _ACTIVE_JOB is not None:
        _finish_job("done", summary=_summary_from_ok(data))
    return 0


def _fail(msg: str) -> int:
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False, indent=2))
    if _ACTIVE_JOB is not None:
        _finish_job("failed", error=msg)
    return 1


def _fail_interrupted(command: str | None, history_error: str | None = None) -> int:
    """用户中断（Ctrl+C）的 JSON 出口（issue #40：契约为先）。

    中断与失败一样守着"结果 JSON 走 stdout"：`ok` 为 false、`error` 是固定
    中文文案，另带机器可读的 `interrupted` 标记与 `kind` / `command`
    （与任务历史同名的字段），脚本据此把"用户中断"与"失败 / 崩溃"分开。
    退出码 130 = 128 + SIGINT，与 shell 里 Ctrl+C 的口径一致，也不与
    "参数错误 / 失败 = 1"混淆。

    历史仍按既有语义收尾：状态 error（tests/test_cli_job_history.py 的中断约定）；
    取消标志触发的路径传自己的 `history_error`，别把 PipelineCancelled 记成
    KeyboardInterrupt。
    """
    kind = command or "unknown"
    _log("cancel", f"{_INTERRUPT_ERROR}，结果仍以 JSON 输出（退出码 {EXIT_INTERRUPTED}）。")
    print(json.dumps({"ok": False, "error": _INTERRUPT_ERROR,
                      "interrupted": True, "kind": kind, "command": kind},
                     ensure_ascii=False, indent=2), flush=True)
    if _ACTIVE_JOB is not None:
        _finish_job("error", error=history_error
                    or f"KeyboardInterrupt: {_INTERRUPT_ERROR}")
    return EXIT_INTERRUPTED


# --- 任务历史：CLI 任务与 Web 写同一份 jobs.json ---------------------------
# 只登记真正的任务命令；env / history 是查询类命令，执行本身不进历史。
# 收尾统一走 _ok / _fail / main 兜底，任何历史写入失败都只打 stderr 警告，
# 绝不覆盖结果 JSON 与退出码。
_TASK_COMMANDS = frozenset({"analyze", "optimize", "full", "package",
                            "slimapk", "slimfont"})
_SUMMARY_TEXT_LIMIT = 512     # 路径/短文案留这么多字符，超长截断
_SUMMARY_LIST_LIMIT = 20      # built 之类小列表最多留这么多项
_CLI_HEARTBEAT_INTERVAL = 30.0
_ACTIVE_JOB: dict | None = None


def _job_title(args) -> str:
    """任务标题取输入路径（path/apk/font），都没有就留空。"""
    for attr in ("path", "apk", "font"):
        value = getattr(args, attr, None)
        if value:
            return str(value)
    return ""


def _job_heartbeat_loop(job_id: str, stop: threading.Event) -> None:
    """CLI 任务存活期间定期刷新历史；失败只记 stderr，不影响任务。"""
    while not stop.wait(_CLI_HEARTBEAT_INTERVAL):
        try:
            jobstore.heartbeat(job_id)
        except Exception as exc:
            _log("history", f"刷新任务心跳失败（任务继续执行）：{exc}")


def _start_job_heartbeat(job: dict) -> None:
    """为单个 CLI 任务启动 daemon 心跳线程；启动失败不影响任务。"""
    stop = threading.Event()
    thread = threading.Thread(
        target=_job_heartbeat_loop,
        args=(job["id"], stop),
        name=f"renpyslim-cli-heartbeat-{job['id']}",
        daemon=True,
    )
    job["heartbeat_stop"] = stop
    job["heartbeat_thread"] = thread
    try:
        thread.start()
    except Exception as exc:
        _log("history", f"启动任务心跳失败（任务继续执行）：{exc}")


def _stop_job_heartbeat(job: dict) -> None:
    """停止并回收心跳线程，保证结束后不会再改写历史记录。"""
    stop = job.get("heartbeat_stop")
    if stop is not None:
        stop.set()
    thread = job.get("heartbeat_thread")
    if (thread is not None and thread is not threading.current_thread()
            and thread.is_alive()):
        thread.join()


def _start_job(args) -> None:
    """任务命令开跑前登记一条历史；登记失败不影响任务本身。"""
    global _ACTIVE_JOB
    _ACTIVE_JOB = None
    command = getattr(args, "command", None)
    if command not in _TASK_COMMANDS:
        return
    job = {"id": uuid.uuid4().hex[:8], "finished": False}
    try:
        jobstore.record_start(job["id"], command,
                              title=_job_title(args), pid=os.getpid())
    except Exception as exc:
        _log("history", f"登记任务历史失败（任务继续执行）：{exc}")
        return
    _start_job_heartbeat(job)
    _ACTIVE_JOB = job


def _finish_job(status: str, summary=None, error=None) -> None:
    """active job 收尾；只写一次，历史写入异常不上抛。"""
    job = _ACTIVE_JOB
    if not job or job.get("finished"):
        return
    job["finished"] = True
    # 先停止并 join 心跳，再写终态，避免后台线程在 record_finish 后
    # 继续刷新 heartbeat；Event 可立即唤醒正在等待的线程。
    _stop_job_heartbeat(job)
    try:
        jobstore.record_finish(job["id"], status,
                               summary=summary, error=error)
    except Exception as exc:
        _log("history", f"任务历史写入失败（不影响本次结果）：{exc}")


def _summary_text(value) -> str | None:
    if isinstance(value, Path):
        value = str(value)
    if not isinstance(value, str) or not value:
        return None
    return value[:_SUMMARY_TEXT_LIMIT]


def _absorb_summary(summary: dict, data) -> None:
    """把一层结果里的摘要字段并进 summary（只收标量与小列表）。"""
    if not isinstance(data, dict):
        return
    saved = data.get("saved_bytes")
    if isinstance(saved, (int, float)) and not isinstance(saved, bool):
        summary["saved_bytes"] = saved
    signed = data.get("signed")
    if isinstance(signed, bool):
        summary["signed"] = signed
    for key in ("output", "working_dir", "archive_output", "changelog",
                "destination"):
        text = _summary_text(data.get(key))
        if text is not None:
            summary[key] = text
    changes = data.get("changes")
    if isinstance(changes, (list, tuple, set, frozenset)):
        summary["changes"] = len(changes)
    elif isinstance(changes, int) and not isinstance(changes, bool):
        summary["changes"] = changes
    built = data.get("built")
    if isinstance(built, (list, tuple)):
        summary["built"] = [str(item)[:_SUMMARY_TEXT_LIMIT]
                            for item in built[:_SUMMARY_LIST_LIMIT]]


def _first_output(result: dict) -> str | None:
    """slimfont 结果只有 outputs 列表，抽第一条产出路径。"""
    outputs = result.get("outputs")
    if not isinstance(outputs, (list, tuple)) or not outputs:
        return None
    first = outputs[0]
    if isinstance(first, dict):
        return _summary_text(first.get("out"))
    return _summary_text(first)


def _summary_from_ok(data: dict) -> dict:
    """从成功结果 JSON 抽小字段做历史摘要；绝不放整份报告/清单/大列表。"""
    summary: dict = {}
    if not isinstance(data, dict):
        return summary
    # optimize / slimapk / slimfont 走 result 包装；package 的 built 也在里面
    _absorb_summary(summary, data)
    result = data.get("result")
    if isinstance(result, dict):
        _absorb_summary(summary, result)
        if "output" not in summary:
            first = _first_output(result)
            if first is not None:
                summary["output"] = first
    # analyze：报告只取规模类字段与条目数，entries 明细不进历史
    report = data.get("report")
    if isinstance(report, dict):
        mode = _summary_text(report.get("mode"))
        if mode is not None:
            summary["mode"] = mode
        for key in ("total_size", "charset_size"):
            number = report.get(key)
            if isinstance(number, (int, float)) and not isinstance(number, bool):
                summary[key] = number
        entries = report.get("entries")
        if isinstance(entries, (list, tuple)):
            summary["entries"] = len(entries)
    # APK 只读预分析汇总（analyze .apk 时出现）
    apk = data.get("apk")
    if isinstance(apk, dict):
        for key in ("optimizable", "est_saving", "est_saving_remap",
                    "signed", "apk_size"):
            value = apk.get(key)
            if isinstance(value, bool) or isinstance(value, (int, float)):
                summary[key] = value
    # full：optimize 与 package 摊平到同一层
    for nested in ("optimize", "package"):
        child = data.get(nested)
        if isinstance(child, dict):
            _absorb_summary(summary, child)
    return summary


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


def _restore_sigint() -> None:
    """把 SIGINT 还给默认处理器：打包段要靠它变成 KeyboardInterrupt。

    取消回调只在优化段有用（置标志、落部分清单）；打包是一条外部命令，
    没人查那个标志，继续留着处理器会把 Ctrl+C 无声吞掉——用户以为停了，
    SDK 还在后台写产物。恢复默认处理器后信号重新变成 KeyboardInterrupt：
    procutil.run_quiet 照旧杀子进程树，cli.main 兜底落中断 JSON 并退 130。
    """
    try:
        signal.signal(signal.SIGINT, signal.default_int_handler)
    except ValueError:
        pass   # 同上：非主线程注册不了，忽略


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

    # APK 自动识别：只读预分析，不解包、不生成工作目录、不改原包。
    # 显式传 project/dist 时按“apk 优先”纠正并警告（与压缩包输入口径一致）。
    if path.suffix.lower() == ".apk":
        mode_arg = getattr(args, "mode", None)
        if mode_arg in ("project", "dist"):
            print(f"警告：{path.name} 是 APK，--mode {mode_arg} 不适用，"
                  "已自动改用 apk 只读预分析。", file=sys.stderr, flush=True)
        from rtools import apk
        try:
            result = apk.analyze_apk(str(path), progress=Progress(_log))
        except apk.ApkError as e:
            return _fail(str(e))
        return _ok({"report": result["report"].to_dict(),
                    "apk": result["apk"]})
    elif getattr(args, "mode", None) == "apk":
        return _fail(f"--mode apk 只适用于 .apk 文件：{args.path}")

    # 压缩包直接进：解压到临时目录，分析完清理
    cleanup_dir = None
    archive_input = False
    if archives.is_archive(str(path)):
        archive_input = True
        import tempfile
        cleanup_dir = tempfile.mkdtemp(prefix="rtools_unpack_")
        try:
            archives.extract_archive(str(path), cleanup_dir, args.password)
            roots = archives.find_dist_roots(cleanup_dir)
            if len(roots) > 1:
                import shutil
                names = "；".join(Path(x).name for x in roots)
                shutil.rmtree(cleanup_dir, ignore_errors=True)
                cleanup_dir = None
                return _fail(
                    f"压缩包里找到 {len(roots)} 个成品目录（{names}）。"
                    "本工具一次只分析一个成品，请把各平台拆开分别分析。")
            path = Path(roots[0])
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
        # 工程有 .rpy 源码，成品只有 .rpyc，以此自动区分。
        # 压缩包一律按成品（dist）：包内可能带源码，但输入形态是发布包，
        # 口径必须与 optimize 一致（AGENTS「压缩包一律按成品处理」）。
        mode = args.mode
        if archive_input:
            if mode and mode != "dist":
                print(f"警告：{path.name} 是压缩包，按成品（dist）分析，"
                      f"--mode {mode} 不适用，已自动改用 dist。",
                      file=sys.stderr, flush=True)
            mode = "dist"
        elif not mode:
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
        # optimize 没有 --sdk：AV1 门控需要 SDK 版本兜底时由 find_sdk 按
        # 配置/常见安装位置自行探测（与打包、lint 的口径一致）。
        if mode == "project":
            result = pipeline.run_project(str(path), opts, work_root,
                                          output_dir, progress, cancel=cancel)
        else:
            # run_dist_smart 兼容目录与压缩包输入，压缩包会自动回包
            result = pipeline.run_dist_smart(str(path), opts, work_root,
                                             output_dir, progress,
                                             password=args.password,
                                             cancel=cancel)
    except pipeline.PipelineCancelled:
        # Ctrl+C 触发的取消走中断出口（issue #40）：JSON + 130，
        # 不再以 -2 收场；子进程与临时目录已由 pipeline 自身的
        # 清理逻辑处理完，这里只负责结果契约。
        return _fail_interrupted("optimize", _CANCELLED_HISTORY_ERROR)
    except scanner.ScanCancelled:
        # 扫描段按 Ctrl+C 时 scanner 抛的是它自己的取消异常（旧版落进
        # "意外错误" + 崩溃转储，把用户中断当成崩溃记）；同样是用户中断。
        return _fail_interrupted("optimize", _SCAN_CANCELLED_HISTORY_ERROR)
    except (pipeline.PipelineError, archives.ArchiveError,
            backup.BackupError) as e:
        return _fail(str(e))
    result.pop("report_dict", None)
    result["report"] = str(result["report"])
    result["changelog"] = str(result["changelog"])
    return _ok({"result": result})


def _parse_platforms(value) -> list[str]:
    """逗号分隔的平台参数 → 列表（空串得空列表，交给统一校验报错）。"""
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def _validated_platforms(value) -> list[str]:
    """平台校验（与 Web 同一口径）；非法时抛 ValueError 由调用方转 JSON。"""
    return packager.validate_platforms(_parse_platforms(value))


def cmd_package(args) -> int:
    # 平台校验先于 SDK 查找与任何目录创建：空/未知平台直接 JSON 失败
    try:
        platforms = _validated_platforms(args.platforms)
    except ValueError as e:
        return _fail(str(e))
    sdk = packager.find_sdk(args.sdk)
    if not sdk:
        return _fail("找不到 Ren'Py SDK，请用 --sdk 指定 SDK 目录。")
    try:
        result = packager.package_project(sdk, args.path, platforms,
                                          args.destination, log=_log_wrapper,
                                          archive_rpa=args.archive_rpa)
    except Exception as e:
        # 审核修复（中-32）：不只捕 FileNotFoundError，权限/超时等
        # 同样要以 JSON 出口收场
        return _fail(str(e))
    if result.get("errors"):
        # 官方 SDK 非零退出时 package_project 只把错误放进 errors；
        # 任一所请求平台失败都必须 ok:false——部分成功（built 非空）
        # 也一样，否则自动化会把半失败当成功。
        partial = ("（已成功：" + "、".join(result["built"]) + "）"
                   if result.get("built") else "")
        return _fail("打包失败" + partial + "："
                     + "；".join(result["errors"])[:800])
    return _ok({"sdk": sdk, "result": result})


def _log_wrapper(msg: str) -> None:
    _log("package", msg)


def cmd_full(args) -> int:
    """模式 A 完整流程：工程优化 -> 官方打包。"""
    path = Path(args.path)
    if not (path / "game").is_dir():
        return _fail(f"{args.path} 不是有效的 Ren'Py 工程（缺少 game 目录）")
    # 平台校验必须在优化开跑之前：非法平台不进入流水线、不建 _rtools_work
    try:
        platforms = _validated_platforms(args.platforms)
    except ValueError as e:
        return _fail(str(e))
    opts = _build_options(args)
    work_root = args.work_root or str(path.parent / "_rtools_work")
    output_dir = args.output or str(path.parent / "_rtools_output")
    Path(work_root).mkdir(parents=True, exist_ok=True)
    progress = Progress(_log)
    cancel = _make_cancel()   # 审核修复（中-3）：Ctrl+C 走取消路径
    try:
        opt_result = pipeline.run_project(str(path), opts, work_root,
                                          output_dir, progress, cancel=cancel,
                                          sdk=args.sdk)
    except pipeline.PipelineCancelled:
        return _fail_interrupted("full", _CANCELLED_HISTORY_ERROR)
    except scanner.ScanCancelled:      # 扫描段中断，同 optimize
        return _fail_interrupted("full", _SCAN_CANCELLED_HISTORY_ERROR)
    except (pipeline.PipelineError, backup.BackupError) as e:
        return _fail(str(e))

    # 优化段结束：打包段不再需要取消标志，把 SIGINT 恢复成默认语义
    # （issue #40——"打包过程中按 Ctrl+C"必须真的能中断，且以 JSON + 130 收场）
    _restore_sigint()

    sdk = packager.find_sdk(args.sdk)
    if not sdk:
        return _fail("优化完成，但找不到 Ren'Py SDK，无法打包。请用 --sdk 指定。")
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
    if pkg_result.get("errors"):
        # 同 cmd_package：任一所请求平台失败都不能报 ok:true
        partial = ("（已成功：" + "、".join(pkg_result["built"]) + "）"
                   if pkg_result.get("built") else "")
        return _fail("优化已完成，但打包失败" + partial + "："
                     + "；".join(pkg_result["errors"])[:800])
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


def _history_limit(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("必须是整数")
    if not 1 <= number <= jobstore.MAX_JOBS:
        raise argparse.ArgumentTypeError(
            f"取值必须在 1..{jobstore.MAX_JOBS} 之间")
    return number


def cmd_history(args) -> int:
    """查看或清空本机任务历史。"""
    if args.clear:
        return _ok({"cleared": jobstore.clear()})
    return _ok({"jobs": jobstore.list_jobs(limit=args.limit)})


def main(argv=None) -> int:
    ap = _JsonArgumentParser(prog="renpyslim", description="RenPySlim：Ren'Py 资源瘦身与打包工具箱（无头模式）")
    ap.add_argument("--version", "-V", action="version", version=f"renpyslim {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("env", help="环境体检（SDK/FFmpeg/Java）")
    p.add_argument("--sdk", default=None)
    p.set_defaults(func=cmd_env)

    # 子解析器默认继承主解析器类（_JsonArgumentParser），无需逐个指定。
    p = sub.add_parser("analyze", help="扫描分析资源（.apk 自动走只读预分析）")
    p.add_argument("path")
    p.add_argument("--mode", choices=["project", "dist", "apk"], default=None)
    # 审核修复：--work-root 从未被 analyze 使用，死参数移除。
    p.add_argument("--password", default=None,
                   help="压缩包密码（如有；zip/7z 支持，tar 系无密码）")
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
        p.add_argument("--password", default=None,
                       help="压缩包密码（成品瘦身直接输压缩包时用；"
                            "zip/7z 支持，tar 系无密码）")
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

    p = sub.add_parser("history", help="查看或清理本机任务历史")
    p.add_argument("--limit", type=_history_limit,
                   default=jobstore.DEFAULT_LIMIT,
                   help=f"最多返回几条（1..{jobstore.MAX_JOBS}，默认 "
                        f"{jobstore.DEFAULT_LIMIT}）")
    p.add_argument("--clear", action="store_true", help="清空任务历史")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("package", help="调用官方 SDK 打包")
    p.add_argument("path")
    p.add_argument("--platforms", default="pc")
    p.add_argument("--destination", default=None)
    p.add_argument("--sdk", default=None)
    p.add_argument("--archive-rpa", action="store_true",
                   help="打包时把资源封入 main.rpa（官方通道）")
    p.set_defaults(func=cmd_package)

    args = ap.parse_args(argv)
    _start_job(args)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        # issue #40：Ctrl+C 不再以 -2（SIGINT）裸奔收场。信号处理器已把
        # 取消标志置位、子进程与临时目录清理也都跑完，这里只补 JSON 出口
        # 与 130 退出码；finally 里的兜底不会再改写已落的历史终态。
        return _fail_interrupted(getattr(args, "command", None))
    except Exception as e:
        # 审核修复（中-32）：统一顶层兜底，任何异常都保证以结果
        # JSON 收场，不再裸 traceback 破坏 stdout 契约。
        # 兜底时落一份含堆栈的崩溃转储（~/.renpyslim/crashes/），
        # 事后追查有迹可循；堆栈只进转储文件与 stderr 日志，
        # stdout 仍然只有合法结果 JSON。
        # 历史先按 "error" 收尾：随后的 _fail 见已收尾便不再改写状态。
        _finish_job("error", error=f"意外错误：{type(e).__name__}: {e}")
        dump = crashdump.write_crash(
            f"cli-{getattr(args, 'command', 'unknown')}")
        if dump:
            _log("crash", f"完整堆栈已存转储：{dump}")
            return _fail(f"意外错误：{type(e).__name__}: {e}"
                         f"（完整堆栈已存：{dump}）")
        return _fail(f"意外错误：{type(e).__name__}: {e}")
    finally:
        # 兜底收尾：Ctrl+C 等 BaseException、或命令既没走 _ok 也没走 _fail
        # 的路径同样落一条 "error"，不留永久 running 的历史幽灵记录。
        # 已在 _ok / _fail / 上面 except 收尾过的任务不会重复写。
        pending = sys.exc_info()[1]
        if isinstance(pending, BaseException):
            _finish_job("error",
                        error=f"{type(pending).__name__}: {pending}")
        else:
            _finish_job("error", error="任务未正常收尾（提前退出）")


if __name__ == "__main__":
    sys.exit(main())
