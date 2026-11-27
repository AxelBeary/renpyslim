"""只读分析作业：Web 与 CLI 共用的唯一实现。

为什么要有这个模块：分析逻辑（扫描 → 分模式分析 → 字符集 → 字体使用
处数）本来在 `cli.cmd_analyze` 和 `web.app.api_analyze` 里**各写了一份**。
两份都能跑，但它们是两份会各自漂移的口径——实测已经漂了：压缩包输入
在 CLI 走 `find_dist_roots`（多个成品直接拒绝，要求拆包），Web 走
`find_dist_root`（只取第一个，其余平台静默丢弃）；同一条 zip 在 GUI
里被判定为 project、在 CLI 里被判定为 dist。

所以这里把"分析一个输入"整件事收成一个函数，两个入口只负责各自的
外壳（CLI 打 JSON、Web 交给后台线程 + 进度回调）。要改分析口径，
只改这一个文件。
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Callable, Optional

from . import analyzer, archives, charset, cleanup, scanner
from .config import CharsetOptions
from .models import AnalysisReport, AssetKind
from .refs import RefIndex

# 进度回调签名：(stage, message, code, params)。两个入口都转成自己的
# 表现（CLI 写 stderr、Web 写任务日志），分析逻辑本身不关心。
LogFn = Callable[..., None]


class AnalyzeError(Exception):
    """分析失败（输入不合法、压缩包损坏、多成品未拆包等）。

    与 PipelineError 同等地位：调用方翻译成各自入口的错误形态
    （CLI 的 ok:false JSON、Web 的 job 失败），不属于"意外错误"。
    """


def _noop_log(stage: str, message: str, code: str = "",
              params: Optional[dict] = None) -> None:
    pass


def detect_mode(path: Path) -> str:
    """工程有 .rpy 源码脚本，成品只有编译后的 .rpyc，以此区分。"""
    game = Path(path) / "game"
    if game.is_dir() and any(game.rglob("*.rpy")):
        return "project"
    return "dist"


def _pick_dist_root(cleanup_dir: str) -> Path:
    """从解压目录里挑出唯一的成品根；多个成品一律拒绝。

    CLI 早就这么做（"本工具一次只处理一个成品，请把各平台拆开"），
    Web 旧版只取第一个、其余平台静默丢弃——同一份输入两个入口给出
    不同结果，属于必须消除的口径分叉。
    """
    roots = archives.find_dist_roots(cleanup_dir)
    if len(roots) > 1:
        names = "；".join(Path(x).name for x in roots)
        raise AnalyzeError(
            f"压缩包里找到 {len(roots)} 个成品目录（{names}）。"
            "本工具一次只分析一个成品，请把各平台拆开分别分析。")
    return Path(roots[0])


def _analyze_project(game: Path, progress) -> tuple[AnalysisReport, str]:
    """工程模式：扫描 + 分析 + 字符集 + 字体使用处数。

    返回 (报告, 可打印字符清单)。CLI 与 Web 用同一份清单，
    不再各算一遍（旧版只有 CLI 产出它，Web 单独拼字符串）。
    """
    scan_log = lambda i, t, n: progress("scan", f"扫描资源 {i}/{t}：{n}",
                                        "scan_progress",
                                        {"i": i, "total": t, "name": n})
    assets = scanner.scan_assets(str(game), probe=True, progress=scan_log,
                                 cancel=_cancel_of(progress))
    report = analyzer.analyze(assets, str(game), "project")
    progress("analyze", "正在提取字符集…", "charset_start")
    chars, warns = charset.extract_charset(str(game), CharsetOptions())
    report.warnings.extend(warns)
    report.charset_size = len(chars)
    report.languages = charset.detect_languages(str(game))

    # 字体使用处数：让用户在优化前就看到哪些字体只用在一两处。
    # 与优化路径共用 cleanup 的实现，不是另写一份统计。
    ref_index = RefIndex(str(game))
    fonts = [a for a in assets if a.kind == AssetKind.FONT
             and a.ext in (".ttf", ".otf")]
    report.font_usage, usage_warns = cleanup.font_usage_report(ref_index, fonts)
    report.warnings.extend(usage_warns)
    charlist = "".join(sorted(c for c in chars if c.isprintable()))
    return report, charlist


def _analyze_dist(path: Path, progress, extract_scripts: bool) -> AnalysisReport:
    """成品模式：散落资源 + 封包内资源，全程只读。

    分析必须只读：解包封包用临时目录，用完立即清理。
    extract_scripts 与优化执行路径保持一致（中-25：分析口径与执行口径
    不一致是潜伏地雷）。
    """
    scan_log = lambda i, t, n: progress("scan", f"扫描资源 {i}/{t}：{n}",
                                        "scan_progress",
                                        {"i": i, "total": t, "name": n})
    work = Path(tempfile.mkdtemp(prefix="rtools_analyze_"))
    try:
        loose = scanner.scan_assets(str(path), probe=True, progress=scan_log,
                                    cancel=_cancel_of(progress))
        packed = scanner.scan_rpa_assets(str(path), str(work), probe=True,
                                         progress=scan_log,
                                         cancel=_cancel_of(progress),
                                         extract_scripts=extract_scripts)
        report = analyzer.analyze(loose + packed, str(path), "dist")
        report.languages = charset.detect_languages(str(path))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    # 防误用：目录里找不到编译脚本，很可能选错了目录
    if not any(f.suffix.lower() == ".rpyc" for f in path.rglob("*.rpyc")):
        report.warnings.insert(
            0, "这个目录里没找到 .rpyc 编译脚本，看起来不像已打包的成品。"
               "请确认选的是解压后含 exe 和 game 文件夹的那个目录；"
               "如果你选的其实是工程目录，请切到左边的「超级打包器」标签。")
    return report


def _cancel_of(progress) -> Optional[Callable[[], bool]]:
    """进度回调对象自带的取消探针（Web 有、CLI 没有）。"""
    return getattr(progress, "cancel", None)


def analyze_input(path: str, mode: Optional[str] = None,
                  password: Optional[str] = None,
                  progress=None) -> dict:
    """分析一个输入（工程目录 / 成品目录 / 压缩包），返回结果 dict。

    path     要分析的路径（.apk 请走 apk.analyze_apk，不经过这里）
    mode     显式模式 project / dist；None 表示自动判断。压缩包输入
             一律按成品（dist）处理——包内可能带源码，但输入形态是
             发布包，口径必须与 optimize 一致（AGENTS「压缩包一律按
             成品处理」）。
    password 压缩包密码
    progress 进度回调 (stage, message, code, params)；也用它携带
             取消探针（progress.cancel），Web 的取消按钮因此生效

    返回 {"mode": ..., "report": {...}, ["charlist": ...]}。
    失败抛 AnalyzeError（输入问题）或原样上抛其它异常。
    """
    raw_path = Path(path)
    if not raw_path.exists():
        raise AnalyzeError(f"路径不存在：{path}")

    progress = progress or _noop_log
    cleanup_dir: Optional[str] = None
    try:
        target = raw_path
        archive_input = archives.is_archive(str(raw_path))
        if archive_input:
            progress("unpack", f"正在解压压缩包 {raw_path.name}…",
                     "unpack_start", {"name": raw_path.name})
            cleanup_dir = tempfile.mkdtemp(prefix="rtools_unpack_")
            try:
                archives.extract_archive(str(raw_path), cleanup_dir, password)
                target = _pick_dist_root(cleanup_dir)
            except archives.ArchiveError as exc:
                raise AnalyzeError(str(exc)) from exc
            progress("unpack", f"已定位成品目录：{target.name}",
                     "unpack_done", {"name": target.name})

        if archive_input:
            # 压缩包一律 dist；显式传 project 只警告，不改行为
            if mode and mode != "dist":
                progress("warn",
                         f"{raw_path.name} 是压缩包，按成品（dist）分析，"
                         f"--mode {mode} 不适用，已自动改用 dist。",
                         "mode_corrected", {"from": mode, "to": "dist"})
            mode = "dist"
        elif not mode:
            mode = detect_mode(target)

        progress("analyze", f"扫描 {raw_path.name}（{mode} 模式）",
                 "analyze_start", {"mode": mode})
        if mode == "project":
            if not (target / "game").is_dir():
                raise AnalyzeError(f"{path} 缺少 game 目录，不是有效工程")
            report, charlist = _analyze_project(target / "game", progress)
            progress("done", "分析完成", "analyze_done")
            return {"mode": "project", "report": report.to_dict(),
                    "charlist": charlist}

        report = _analyze_dist(target, progress, extract_scripts=True)
        progress("done", "分析完成", "analyze_done")
        return {"mode": "dist", "report": report.to_dict()}
    finally:
        if cleanup_dir:
            shutil.rmtree(cleanup_dir, ignore_errors=True)
