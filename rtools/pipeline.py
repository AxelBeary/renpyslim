"""流水线编排：模式 A（工程优化）与模式 B（成品瘦身）。

安全原则贯穿始终：
- 默认在自动生成的工作副本上操作，原件不动；
- 每个优化器都遵循"没变小就不替换"；
- 找不到字面引用的资源绝不改名，只原地压缩；
- 全部改动写入修改清单。
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable, Optional

from . import analyzer, apk, archives, backup, cache, charset, cleanup, font_tool, procutil, scanner, verifier
from . import remap as remap_mod
from .audio_optimizer import convert_audio, reencode_audio, find_ffmpeg
from .font_optimizer import subset_font
from .image_optimizer import optimize_image, quantize_png
from .video_optimizer import compress_video
from .models import AssetKind, ChangeRecord, Progress
from .config import OptimizeOptions
from .refs import RefIndex
from .utils import fmt_size as _fmt, find_suffix_clashes
from . import rpa


class PipelineError(Exception):
    pass


class PipelineCancelled(PipelineError):
    """用户主动取消（区别于真错误：不写崩溃转储）。

    审核修复（中-1）：携带被取消时本批已完成的结果，
    否则这些已落盘的改动不会进 changelog。
    """

    def __init__(self, partial_results: list | None = None):
        super().__init__("cancelled")
        self.partial_results = partial_results or []


def _find_game_dir(project_dir: str) -> Path:
    game = Path(project_dir) / "game"
    if not game.is_dir():
        raise PipelineError(
            f"在 {project_dir} 里找不到 game 目录，这不是一个有效的 Ren'Py 工程。"
        )
    return game


def _flush_partial_changelog(output_dir: str, records, saved) -> None:
    """取消/中断时把已完成的改动落清单（审核修复）。

    否则磁盘已被部分修改，清单却不存在，用户无从知道发生了什么。
    """
    try:
        _write_json(Path(output_dir) / "changelog.json",
                    {"records": [r.to_dict() for r in records],
                     "saved_bytes": saved, "cancelled": True})
    except Exception:
        pass


def _run_jobs_or_flush(p: Progress, stage: str, jobs: list, cancel,
                       output_dir: str, records, saved) -> list:
    """_run_jobs 外加中断兜底：任何异常都先把已落盘的改动记入清单。

    审核修复：
    - 中-1：取消时本批已完成的改动先聚合再落清单；
    - 高-5：非取消的真异常（文件占用/磁盘满等）同样落清单，
      磁盘已被部分修改时用户才有对账依据。
    """
    try:
        return _run_jobs(p, stage, jobs, cancel)
    except PipelineCancelled as e:
        extra = 0
        for r in e.partial_results:
            records.extend(r.get("records", []))
            extra += r.get("saved", 0)
        _flush_partial_changelog(output_dir, records, saved + extra)
        raise
    except BaseException:
        _flush_partial_changelog(output_dir, records, saved)
        raise


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 第二波修复：原子写——先写同目录临时文件再 os.replace，
    # 写到一半崩溃/被杀不会留下半截环的报告文件；
    # 异常时清掉临时文件。
    # 收口修复：临时文件名含 .rtools. 标记，硬杀残留也能被
    # cleanup._is_rtools_tmp 识别并在下次运行时清掉。
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.rtools.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _check_cancel(cancel: Callable[[], bool] | None) -> None:
    """后段各步骤之间的取消检查（第二波修复）。

    在磁盘变更密集段（引用改写/封包重建/报告落盘）的每一步之前快速检查，
    取消时抛 PipelineCancelled，由调用处既有的 except 层落部分清单。
    """
    if cancel and cancel():
        raise PipelineCancelled()


def _worker_count(n_jobs: int) -> int:
    """并行度决策（用户拍板放开多核）。

    小批量串行即可；否则用满可用核心（留 2 个给系统），
    上限 16 防超多核机器 IO 踩踏。旧版写死最高 6 路，
    28 核机器上大部分核心在睡觉。
    """
    if n_jobs < 4:
        return 1
    return max(2, min(16, (os.cpu_count() or 4) - 2))


def _safe_job(kind: str, item: tuple) -> tuple:
    """job 级异常兜底（第二波修复）：优化器内部异常以前被 _run_jobs 吞掉
    且不计账，失败数虚低；这里按所属类型归因计 failed。
    记账以返回 dict 的 failed/skipped 字段为准。
    """
    label, fn = item

    def wrapped():
        try:
            return fn()
        except Exception as e:
            return {"records": [], "saved": 0, "rename": None, "remap": None,
                    "rpa": None, "warn": None, "failed": kind,
                    "skipped": None, "exception": str(e)}
    return label, wrapped


def _run_jobs(p: Progress, stage: str, jobs: list,
              cancel: "Optional[Callable[[], bool]]" = None) -> list:
    """并行执行一批独立优化任务（BACKLOG B4）。

    jobs: [(标签, 无参callable)]，callable 自己负责 try/except 并返回 dict 或 None。
    小批量退化为串行；进度按完成数 + 累计节省字节汇报（F6）；
    取消时不再等待未开始的任务（F4），短超时轮询保证取消秒级生效（第二波修复）。
    """
    if not jobs:
        return []
    workers = _worker_count(len(jobs))
    results: list = []
    state = {"done": 0, "saved": 0}
    lock = threading.Lock()

    def _cancel_now(pending):
        """取消收尾：取消未开始的、杀掉正在跑的外部程序、
        收集已完成的成果（只拿还没被处理过的，防重复记账）。"""
        for rest in futures:
            rest.cancel()
        # 审核修复（中-3）：杀掉正在跑的外部程序（ffmpeg 等），
        # 否则线程池退出要等它们自然跑完，取消形同虚设
        procutil.kill_children()
        for rest in pending:
            if rest.done() and not rest.cancelled():
                try:
                    r = rest.result(timeout=0)
                except Exception:
                    r = None
                if r:
                    with lock:
                        state["saved"] += r.get("saved", 0)
                    results.append(r)
        raise PipelineCancelled(results)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(fn) for _label, fn in jobs]
        try:
            # 第二波修复：旧版阻塞在 as_completed 上，单个长任务（视频可达 7200s）
            # 没完成前根本轮不到取消检查；改为短超时轮询，
            # 每段等待间隙都检查取消，取消生效延迟控制在秒级。
            pending = set(futures)
            while pending:
                if cancel and cancel():
                    _cancel_now(pending)
                done, pending = wait(pending, timeout=0.5,
                                     return_when=FIRST_COMPLETED)
                for fut in done:
                    with lock:
                        state["done"] += 1
                        if state["done"] % 5 == 0 or state["done"] == len(futures):
                            p.emit(stage, f"处理中 {state['done']}/{len(futures)}"
                                          f" · 累计已省 {_fmt(state['saved'])}")
                    try:
                        r = fut.result()
                    except Exception as e:
                        # 审核修复（中-2）：吞异常可以，但至少要留条日志；
                        # 第二波修复：异常也计入失败（此前丢弃导致失败数虚低），
                        # 归因由 _safe_job 包装层完成，这里是最后兜底。
                        p.emit(stage, f"任务执行异常，已跳过：{e}")
                        # 收口修复：兜底归因用字符串 "internal"——旧版写布尔
                        # True，在成品模式 isinstance(fk, str) 过滤下不计数，
                        # 失败数虚低。
                        r = {"records": [], "saved": 0, "rename": None,
                             "remap": None, "rpa": None, "warn": None,
                             "skipped": None, "failed": "internal"}
                    if r:
                        with lock:
                            state["saved"] += r.get("saved", 0)
                        results.append(r)
        except PipelineCancelled as e:
            for rest in futures:
                rest.cancel()
            procutil.kill_children()   # 审核修复（中-3）：同取消分支
            # 已完成的成果尽量留下，不强删；并随异常上抛供上层落清单
            raise PipelineCancelled(e.partial_results or results) from None
    return results


# ===========================================================================
# 模式 A：工程优化
# ===========================================================================

def run_project(project_dir: str, options: OptimizeOptions,
                work_root: str, output_dir: str,
                progress: Progress | None = None,
                cancel: Callable[[], bool] | None = None) -> dict:
    """优化一个 Ren'Py 工程。返回汇总 dict（含报告路径）。"""
    p = progress or Progress()
    records: list[ChangeRecord] = []
    preset = options.preset_obj()

    _find_game_dir(project_dir)  # 先验证，避免白复制

    # --- 第 1 步：工作副本 / 直接改原件（C 选项） ---
    if options.in_place:
        p.emit("backup", "直接修改原工程模式：正在生成强制备份压缩包……")
        zip_path = str(Path(project_dir).parent /
                       f"{Path(project_dir).name}-备份-{time.strftime('%Y%m%d-%H%M%S')}.zip")
        backup.make_backup_zip(project_dir, zip_path)
        records.append(ChangeRecord(action="backup", src=project_dir, dst=zip_path,
                                    detail="直接修改原工程前的强制完整备份"))
        working = project_dir
        p.emit("backup", f"备份完成：{zip_path}")
    else:
        p.emit("copy", "正在复制工程到工作副本（原件保持不动）……")
        working = backup.make_working_copy(project_dir, work_root)
        records.append(ChangeRecord(action="copy", src=project_dir, dst=working,
                                    detail="在副本上操作，原件未改动"))
        p.emit("copy", f"工作副本就绪：{working}")

    game_dir = str(_find_game_dir(working))

    # --- 第 2 步：扫描 + 分析 ---
    p.emit("analyze", "正在扫描资源文件……")
    assets = scanner.scan_assets(
        game_dir, probe=True,
        progress=lambda i, t, n: p.emit("scan", f"扫描资源 {i}/{t}：{n}"),
        cancel=cancel)
    report = analyzer.analyze(assets, root=game_dir, mode="project")

    # --- 第 3 步：字符集 ---
    chars, charset_warnings = charset.extract_charset(game_dir, options.charset)
    report.warnings.extend(charset_warnings)
    report.charset_size = len(chars)
    charlist_path = font_tool.write_charlist(
        chars, str(Path(output_dir) / "charlist.txt"))
    p.emit("analyze", f"扫描到 {len(assets)} 个资源文件，实际使用字符 {len(chars)} 个")

    # --- 第 4 步：优化 ---
    ref_index = RefIndex(game_dir)
    rename_map: dict[str, str] = {}
    saved = 0
    min_bytes = preset.min_size_kb * 1024

    # --- 附加检测（只报告，不动手）：废资源 / 重复文件 / 缺字 ---
    unused = cleanup.find_unused_assets(assets, ref_index)
    if unused:
        report.warnings.append(
            f"发现 {len(unused)} 个完全找不到引用的音频/视频/字体（见结果 unused 字段）。"
            "默认不处理；勾选「隔离无用资源」后也只是移入隔离文件夹，不会删除。"
            "图片不参与此项检测：Ren'Py 会按文件名自动加载图片，查不到引用不等于没用。")
    duplicates = cleanup.find_duplicates(assets)
    if duplicates:
        total_waste = sum(d["size"] * (len(d["files"]) - 1) for d in duplicates)
        report.warnings.append(
            f"发现 {len(duplicates)} 组内容完全相同的重复文件，多余副本共占 "
            f"{_fmt(total_waste)}（见结果 duplicates 字段）。请自行确认后手动处理。")
    missing_glyphs: dict[str, list[str]] = {}
    for a in [x for x in assets if x.kind == AssetKind.FONT
              and x.ext in (".ttf", ".otf")]:
        miss = charset.find_missing_glyphs(a.path, chars)
        if miss:
            missing_glyphs[a.rel] = miss
            report.warnings.append(
                f"{a.rel}：脚本用到了 {len(miss)} 个该字体里没有的字，"
                f"如「{''.join(miss[:12])}」等——这些字会显示方框（除非配置了回退字体），"
                "与瘦身无关，瘦身前就存在。")

    todo_images = [a for a in assets if a.kind == AssetKind.IMAGE]
    todo_audio = [a for a in assets if a.kind == AssetKind.AUDIO]
    todo_videos = [a for a in assets if a.kind == AssetKind.VIDEO]
    todo_fonts = [a for a in assets if a.kind == AssetKind.FONT
                  and a.ext in (".ttf", ".otf")]
    # 失败计数（BACKLOG B3：报告口径诚实化）；第二波：skipped 单独分桶，
    # “压完没变小/格式不支持”不再混入失败（旧版一律记 failed 导致失败数虚高）
    failed = {"image": 0, "audio": 0, "video": 0, "font": 0}
    skipped = {"image": 0, "audio": 0, "video": 0, "font": 0}

    def _img_job(a):
        def job():
            out = {"records": [], "saved": 0, "rename": None, "warn": None,
                   "failed": False, "skipped": False}
            # 实验性深度压缩（有损量化）：成功即收工，失败落到常规路径
            if options.png_quant and a.ext == ".png" and a.size >= 64 * 1024:
                h = cache.hash_file(a.path) if options.use_cache else None
                hit = cache.lookup_hash(h, "img|quant256") if h else None
                if hit and cache.apply_cached(hit, a.path):
                    new = Path(a.path).stat().st_size
                    out["saved"] = a.size - new
                    out["records"].append(ChangeRecord(
                        action="quantize", src=a.rel,
                        detail=f"{_fmt(a.size)} -> {_fmt(new)}（量化·缓存命中）"))
                    return out
                res = quantize_png(a.path, a.path)
                if res:
                    if h:
                        cache.store_hash(h, "img|quant256", a.path)
                    out["saved"] = res["old_size"] - res["new_size"]
                    out["records"].append(ChangeRecord(
                        action="quantize", src=a.rel,
                        detail=f"{_fmt(res['old_size'])} -> {_fmt(res['new_size'])}（有损量化）"))
                    return out
            want_webp = (preset.png_to_webp and options.convert_png_webp
                         and a.ext in (".png", ".jpg", ".jpeg"))
            # 审核修复：转换目标与另一资源撞名时降级原地压缩（防互覆）
            if want_webp and str(Path(a.rel).with_suffix(".webp")).replace("\\", "/") in clash_webp:
                want_webp = False
                out["warn"] = (f"{a.rel}：换格式后与另一资源同名，"
                               "已降级为原地压缩，避免互覆。")
            if want_webp:
                if ref_index.find(a.rel):
                    new_rel = str(Path(a.rel).with_suffix(".webp")).replace("\\", "/")
                    new_path = str(Path(game_dir) / new_rel)
                    if Path(new_path).exists():
                        # 审核修复（高-3）：转换目标已被现存资源占用（撞名
                        # 预检有盲区），绝不覆写，降级原地压缩
                        out["warn"] = (f"{a.rel}：换格式目标 {new_rel} 已存在，"
                                       "为避免覆盖已降级为原地压缩。")
                    else:
                        h = cache.hash_file(a.path) if options.use_cache else None
                        key = f"img|q{preset.image_quality}|webp"
                        hit = cache.lookup_hash(h, key) if h else None
                        if hit and cache.apply_cached(hit, new_path):
                            Path(a.path).unlink()
                            new_sz = Path(new_path).stat().st_size
                            out["rename"] = (a.rel, new_rel)
                            out["saved"] = a.size - new_sz
                            out["records"].append(ChangeRecord(
                                action="convert", src=a.rel, dst=new_rel,
                                detail=f"{_fmt(a.size)} -> {_fmt(new_sz)}（缓存命中）"))
                            return out
                        res = optimize_image(a.path, new_path, preset.image_quality,
                                             convert_webp=True)
                        # 记账以 status 为准；非 ok（skipped/failed）落到下方原地压缩
                        if res["status"] == "ok":
                            Path(a.path).unlink()
                            if h:
                                cache.store_hash(h, key, new_path)
                            out["rename"] = (a.rel, new_rel)
                            out["saved"] = res["old_size"] - res["new_size"]
                            out["records"].append(ChangeRecord(
                                action="convert", src=a.rel, dst=new_rel,
                                detail=f"{_fmt(res['old_size'])} -> {_fmt(res['new_size'])}"))
                            return out
                else:
                    out["warn"] = (f"{a.rel}：脚本里找不到字面引用（可能是目录自动加载"
                                   "或变量拼接），为安全起见只原地压缩、不转 WebP。")
            # 常规/兜底：原地压缩
            h = cache.hash_file(a.path) if options.use_cache else None
            key = f"img|q{preset.image_quality}|same"
            hit = cache.lookup_hash(h, key) if h else None
            if hit and cache.apply_cached(hit, a.path):
                new_sz = Path(a.path).stat().st_size
                if new_sz < a.size:
                    out["saved"] = a.size - new_sz
                    out["records"].append(ChangeRecord(
                        action="compress", src=a.rel,
                        detail=f"原地压缩（缓存命中）{_fmt(a.size)} -> {_fmt(new_sz)}"))
                return out
            res = optimize_image(a.path, a.path, preset.image_quality)
            if res["status"] == "ok":
                if h:
                    cache.store_hash(h, key, a.path)
                # 审核修复（中-10）：登记产物自映射，防 in_place 反复
                # 运行时有损重编码代际累积
                if options.use_cache:
                    cache.store_self(a.path, key)
                out["saved"] = res["old_size"] - res["new_size"]
                out["records"].append(ChangeRecord(action="compress", src=a.rel,
                                                   detail="原地无损/低损压缩"))
            elif res["status"] == "skipped":
                out["skipped"] = True
            else:
                out["failed"] = True
            return out
        return a.rel, job

    def _aud_job(a):
        def job():
            out = {"records": [], "saved": 0, "rename": None, "warn": None,
                   "failed": False, "skipped": False}
            if a.ext in (".wav", ".mp3"):
                # 审核修复：转换目标撞名时不换格式（防互覆）
                if str(Path(a.rel).with_suffix(".ogg")).replace("\\", "/") in clash_ogg:
                    out["warn"] = (f"{a.rel}：换格式后与另一资源同名，"
                                   "已跳过格式转换，保留原样。")
                elif ref_index.find(a.rel):
                    new_rel = str(Path(a.rel).with_suffix(".ogg")).replace("\\", "/")
                    new_path = str(Path(game_dir) / new_rel)
                    if Path(new_path).exists():
                        # 审核修复（高-3）：目标已存在（如 wav 源与 ogg 发布版
                        # 并存），绝不覆写，保留原样
                        out["warn"] = (f"{a.rel}：换格式目标 {new_rel} 已存在，"
                                       "为避免覆盖已跳过格式转换。")
                        return out
                    h = cache.hash_file(a.path) if options.use_cache else None
                    key = f"aud|{preset.audio_bitrate_k}|to-ogg"
                    hit = cache.lookup_hash(h, key) if h else None
                    if hit and cache.apply_cached(hit, new_path):
                        Path(a.path).unlink()
                        new_sz = Path(new_path).stat().st_size
                        out["rename"] = (a.rel, new_rel)
                        out["saved"] = a.size - new_sz
                        out["records"].append(ChangeRecord(
                            action="convert", src=a.rel, dst=new_rel,
                            detail=f"{_fmt(a.size)} -> {_fmt(new_sz)}（缓存命中）"))
                        return out
                    res = convert_audio(a.path, new_path, preset.audio_bitrate_k)
                    if res["status"] == "ok":
                        Path(a.path).unlink()
                        if h:
                            cache.store_hash(h, key, new_path)
                        out["rename"] = (a.rel, new_rel)
                        out["saved"] = res["old_size"] - res["new_size"]
                        out["records"].append(ChangeRecord(
                            action="convert", src=a.rel, dst=new_rel,
                            detail=f"{_fmt(res['old_size'])} -> {_fmt(res['new_size'])}"))
                    elif res["status"] == "skipped":
                        out["skipped"] = True
                    else:
                        out["failed"] = True
                else:
                    out["warn"] = f"{a.rel}：找不到字面引用，无法安全换格式，已跳过。"
            elif a.ext == ".ogg" and a.bitrate and \
                    a.bitrate > preset.audio_bitrate_k + 32:
                h = cache.hash_file(a.path) if options.use_cache else None
                key = f"aud|{preset.audio_bitrate_k}|re-ogg"
                hit = cache.lookup_hash(h, key) if h else None
                if hit and cache.apply_cached(hit, a.path):
                    new_sz = Path(a.path).stat().st_size
                    if new_sz < a.size:
                        out["saved"] = a.size - new_sz
                        out["records"].append(ChangeRecord(
                            action="compress", src=a.rel,
                            detail="OGG 降码率（缓存命中）"))
                    return out
                res = reencode_audio(a.path, a.path, preset.audio_bitrate_k)
                if res["status"] == "ok":
                    if h:
                        cache.store_hash(h, key, a.path)
                    # 审核修复（中-10）：同上，防重编码代际累积
                    if options.use_cache:
                        cache.store_self(a.path, key)
                    out["saved"] = res["old_size"] - res["new_size"]
                    out["records"].append(ChangeRecord(action="compress", src=a.rel,
                                                       detail="OGG 降码率重编码"))
                elif res["status"] == "skipped":
                    out["skipped"] = True
                else:
                    out["failed"] = True
            return out
        return a.rel, job

    def _vid_job(a):
        def job():
            out = {"records": [], "saved": 0, "rename": None, "warn": None,
                   "failed": False, "skipped": False}
            h = cache.hash_file(a.path) if options.use_cache else None
            key = f"vid|{options.preset}|{a.ext}"
            hit = cache.lookup_hash(h, key) if h else None
            if hit and cache.apply_cached(hit, a.path):
                new_sz = Path(a.path).stat().st_size
                if new_sz < a.size:
                    out["saved"] = a.size - new_sz
                    out["records"].append(ChangeRecord(
                        action="compress", src=a.rel,
                        detail=f"视频重编码（缓存命中）{_fmt(a.size)} -> {_fmt(new_sz)}"))
                return out
            try:
                res = compress_video(a.path, a.path, options.preset,
                                     use_av1=options.experimental_av1)
            except RuntimeError as e:
                out["warn"] = str(e)
                return out
            if res["status"] == "ok":
                if h:
                    cache.store_hash(h, key, a.path)
                # 审核修复（中-10）：同上，防视频反复重编码代际退化
                if options.use_cache:
                    cache.store_self(a.path, key)
                out["saved"] = res["old_size"] - res["new_size"]
                out["records"].append(ChangeRecord(
                    action="compress", src=a.rel,
                    detail=f"视频重编码 {_fmt(res['old_size'])} -> {_fmt(res['new_size'])}"))
            elif res["status"] == "skipped":
                out["skipped"] = True
                if res.get("reason"):
                    out["warn"] = f"{a.rel}：{res['reason']}"
            else:
                out["failed"] = True
                if res.get("reason"):
                    out["warn"] = f"{a.rel}：视频压缩失败（{res['reason']}）"
            return out
        return a.rel, job

    def _aggregate(results, kind):
        nonlocal saved
        for r in results:
            records.extend(r["records"])
            saved += r["saved"]
            if r["rename"]:
                rename_map[r["rename"][0]] = r["rename"][1]
            if r["warn"]:
                report.warnings.append(r["warn"])
            # 第二波记账口径：以字段为准，skipped 不计失败、单独累计
            if r["failed"]:
                failed[kind] += 1
            if r.get("skipped"):
                skipped[kind] += 1

    # 审核修复：换后缀转换的同名撞车预检（foo.png 与 foo.jpg 都会
    # 变 foo.webp，并行转换会互覆）；撞车项降级为原地压缩
    clash_webp = find_suffix_clashes(
        [a.rel for a in todo_images if a.ext in (".png", ".jpg", ".jpeg")],
        ".webp")
    clash_ogg = find_suffix_clashes(
        [a.rel for a in todo_audio if a.ext in (".wav", ".mp3")], ".ogg")

    if options.do_images:
        jobs = [_safe_job("image", _img_job(a)) for a in todo_images
                if a.size >= min_bytes and a.ext not in (".gif", ".bmp", ".avif")]
        _aggregate(_run_jobs_or_flush(p, "optimize", jobs, cancel,
                                      output_dir, records, saved), "image")

    if options.do_audio:
        if not find_ffmpeg():
            report.warnings.append(
                "未找到 FFmpeg，音频优化已跳过。安装方法（任选其一）："
                "① 打开 PowerShell 运行 winget install Gyan.FFmpeg（推荐，装完重启工具）；"
                "② 到 https://www.ffmpeg.org/download.html 下载，把 ffmpeg.exe 放到本工具旁边的 bin 文件夹。")
        else:
            jobs = [_safe_job("audio", _aud_job(a)) for a in todo_audio
                    if a.size >= min_bytes]
            _aggregate(_run_jobs_or_flush(p, "optimize", jobs, cancel,
                                          output_dir, records, saved), "audio")

    if options.do_videos:
        if not find_ffmpeg():
            report.warnings.append("未找到 FFmpeg，视频压缩已跳过。")
        else:
            report.warnings.append(
                "视频压缩为实验性功能：同名重编码，若个别播放器出现兼容问题，"
                "请在高级选项关闭后重跑。")
            if options.experimental_av1:
                report.warnings.append(
                    "已启用 AV1 视频编码（实验性）：官方支持且体积更小，"
                    "但只有 Ren'Py 8.0 及以上构建的游戏能播放，老引擎会放不出来。")
            jobs = [_safe_job("video", _vid_job(a)) for a in todo_videos
                    if a.ext in (".mp4", ".webm", ".ogv")]
            _aggregate(_run_jobs_or_flush(p, "optimize", jobs, cancel,
                                          output_dir, records, saved), "video")

    if options.do_fonts and preset.font_subset:
        try:
            for i, a in enumerate(todo_fonts, start=1):
                if cancel and cancel():
                    raise PipelineCancelled()
                p.emit("optimize", f"字体 {i}/{len(todo_fonts)}：{a.rel}")
                if a.size < 256 * 1024:      # 小于 256KB 的字体瘦身收益有限
                    continue
                # 字体不走缓存：结果依赖字符集，命中率低、意义小
                try:
                    res = subset_font(a.path, a.path, chars)
                    saved += res["old_size"] - res["new_size"]
                    records.append(ChangeRecord(
                        action="subset_font", src=a.rel,
                        detail=f"字形 {res['glyphs_before']} -> {res['glyphs_after']}，"
                               f"{_fmt(res['old_size'])} -> {_fmt(res['new_size'])}"))
                except Exception as e:
                    failed["font"] += 1
                    report.warnings.append(f"{a.rel}：字体瘦身失败，保留原文件（{e}）")
        except BaseException:
            # 审核修复（高-5）：取消与意外异常都落部分清单，
            # 字体子集化不可逆，已剃的必须有对账依据
            _flush_partial_changelog(output_dir, records, saved)
            raise

    # 第二波记账口径：failed（真错误）与 skipped（已是最优/不支持）分开；
    # “压完没变小”不再计失败，但两者都原样保留、不计节省。
    total_failed = sum(failed.values())
    if total_failed:
        report.warnings.append(
            f"{total_failed} 个文件处理失败（详见日志），已原样保留，未计入节省体积。")
    total_skipped = sum(skipped.values())
    if total_skipped:
        report.warnings.append(
            f"{total_skipped} 个文件处理后未能进一步压缩（可能已是最优或格式不适合），"
            "已原样保留，未计入节省体积。")

    # --- 第 5 步：改写脚本引用 ---
    # 审核修复（高-5）：从这里开始每一步都可能改盘（引用改写/垃圾清理/
    # 隔离/写报告），任何异常都要先把已发生的改动落清单
    try:
        # 第二波：后段每个磁盘变更步骤前都检查取消，取消时抛异常走下方
        # except 落部分清单，不再拖到下一步才发现。
        _check_cancel(cancel)
        if rename_map:
            p.emit("rewrite", f"正在改写 {len(rename_map)} 个资源的脚本引用……")
            records.extend(ref_index.rewrite(rename_map))

        _check_cancel(cancel)
        # --- 第 5.5 步：垃圾清理与废资源隔离 ---
        junk = {"freed_bytes": 0, "removed": []}
        if options.in_place:
            report.warnings.append(
                "直接修改原件模式下跳过了垃圾清理（为保护你的存档和缓存）。")
        else:
            p.emit("clean", "正在清理缓存、日志等可再生垃圾……")
            junk = cleanup.clean_junk(working)
            if junk["removed"]:
                saved += junk["freed_bytes"]
                records.append(ChangeRecord(
                    action="junk_clean", src=working,
                    detail=f"清理 {len(junk['removed'])} 项可再生垃圾，"
                           f"释放 {_fmt(junk['freed_bytes'])}"))

        quarantined: list[str] = []
        if options.quarantine_unused and unused:
            p.emit("clean", f"正在把 {len(unused)} 个无引用资源移入隔离区……")
            # 审核修复：unused 的相对路径以 game/ 为基准，隔离路径也得按
            # game/ 拼——以前按工程根拼，路径对不上，隔离静默失效
            quarantined = cleanup.quarantine_files(game_dir, unused)
            for rel in quarantined:
                records.append(ChangeRecord(action="quarantine", src=rel,
                                            dst=str(Path(game_dir) / "_rtools_quarantine" / rel),
                                            detail="确认无引用，已隔离而非删除"))

        _check_cancel(cancel)
        # --- 第 5.8 步：官方 lint 自动验证 ---
        from . import packager
        validation = {"ran": False, "ok": False, "summary": "未执行", "suspects": []}
        sdk = packager.find_sdk()
        if sdk:
            p.emit("verify", "正在用官方 lint 验证优化后的工程……")
            validation = verifier.lint_project(sdk, working)
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            (Path(output_dir) / "validation.txt").write_text(
                validation.get("output", ""), encoding="utf-8")
            p.emit("verify", f"lint 结果：{validation['summary']}")
            if not validation["ok"]:
                report.warnings.append(
                    f"官方 lint 验证未完全通过：{validation['summary']}。"
                    f"完整输出见 {Path(output_dir) / 'validation.txt'}")
        else:
            report.warnings.append("未找到 Ren'Py SDK，跳过了自动 lint 验证。")

        _check_cancel(cancel)
        # --- 第 6 步：产出报告与修改清单 ---
        out = Path(output_dir)
        _write_json(out / "analysis.json", report.to_dict())
        _write_json(out / "changelog.json",
                    {"records": [r.to_dict() for r in records], "saved_bytes": saved})
    except BaseException:
        _flush_partial_changelog(output_dir, records, saved)
        raise
    p.emit("done", f"工程优化完成，共节省约 {_fmt(saved)}")

    return {
        "mode": "project",
        "working_dir": working,
        "game_dir": game_dir,
        "saved_bytes": saved,
        "report": out / "analysis.json",
        "changelog": out / "changelog.json",
        "warnings": report.warnings,
        "unused": unused,
        "quarantined": quarantined,
        "duplicates": duplicates,
        "missing_glyphs": missing_glyphs,
        "junk": junk,
        "failed": failed,
        "skipped": skipped,
        "charlist": charlist_path,
        # 审核修复（高-1）：verifier 各返回分支键集不完全一致
        # （超时/异常分支无 suspects 等），一律用 get 防 KeyError
        "validation": {k: validation.get(k) for k in ("ran", "ok", "summary", "suspects")},
        "report_dict": report.to_dict(),
    }


# ===========================================================================
# 模式 B：已打包成品瘦身
# ===========================================================================

def run_dist(dist_dir: str, options: OptimizeOptions,
             work_root: str, output_dir: str,
             progress: Progress | None = None,
             cancel: Callable[[], bool] | None = None) -> dict:
    """对一个已打包的 Ren'Py 成品做安全瘦身。不改文件名、不换格式。"""
    p = progress or Progress()
    records: list[ChangeRecord] = []
    preset = options.preset_obj()
    dist_p = Path(dist_dir)
    if not dist_p.is_dir():
        raise PipelineError(f"成品目录不存在：{dist_dir}")

    # --- 第 1 步：工作副本 ---
    if options.in_place:
        p.emit("backup", "直接修改原成品模式：正在生成强制备份压缩包……")
        zip_path = str(dist_p.parent /
                       f"{dist_p.name}-备份-{time.strftime('%Y%m%d-%H%M%S')}.zip")
        backup.make_backup_zip(dist_dir, zip_path)
        records.append(ChangeRecord(action="backup", src=dist_dir, dst=zip_path,
                                    detail="直接修改原成品前的强制完整备份"))
        working = dist_dir
    else:
        p.emit("copy", "正在复制成品到工作副本（原件保持不动）……")
        working = backup.make_working_copy(dist_dir, work_root)
        records.append(ChangeRecord(action="copy", src=dist_dir, dst=working,
                                    detail="在副本上操作，原件未改动"))
    working_p = Path(working)

    # --- 第 2 步：扫描（散落文件 + RPA 封包） ---
    p.emit("analyze", "正在扫描成品资源……")
    extract_dir = working_p / "_rtools_extract"
    scan_log = lambda i, t, n: p.emit("scan", f"扫描资源 {i}/{t}：{n}")
    loose = scanner.scan_assets(working, probe=True, progress=scan_log,
                                cancel=cancel)
    # extract_scripts=True（审核修复）：一并解出封包内脚本，供字符集
    # 提取扫描，否则标准成品（无源码、脚本封 rpa）字体会被剃成保底集
    packed = scanner.scan_rpa_assets(working, str(extract_dir), probe=True,
                                     progress=scan_log, cancel=cancel,
                                     extract_scripts=True)
    all_assets = loose + packed
    report = analyzer.analyze(all_assets, root=working, mode="dist")

    # --- 第 3 步：字符集（成品模式：原始字节扫描法） ---
    chars, warnings = charset.extract_charset_dist(working, options.charset)
    report.warnings.extend(warnings)
    report.charset_size = len(chars)
    p.emit("analyze", f"扫描到 {len(loose)} 个散落资源、{len(packed)} 个封包内资源，"
                      f"实际使用字符 {len(chars)} 个")

    # --- 第 3.4 步：反编译解锁（实验性，用户拍板 2026-08-17） ---
    # 无源码成品的引用被焊死在 rpyc 里；用 vendored unrpyc（MIT）
    # 反编译出 rpy 后，既有的引用改写/格式转换机制全部解锁；
    # 引擎启动时发现 rpy 比 rpyc 新会自动重编译，玩家无感
    game_dir_p = working_p / "game"
    dec_stats = None
    if options.experimental_decompile and game_dir_p.is_dir():
        from . import decompile as _decompile
        import shutil as _sh_dec
        p.emit("decompile", "正在反编译编译脚本（解锁格式转换能力）……")
        dec_stats = _decompile.decompile_scripts(str(game_dir_p))
        # 封包内脚本：解出的 rpyc 也反编译，产物拷回 game/ 对应位置，
        # 让引用索引扫得到，且引擎加载时优先于封包内同名 rpyc
        if extract_dir.exists():
            _decompile.decompile_scripts(str(extract_dir))
            for f in (list(extract_dir.rglob("*.rpy"))
                      + list(extract_dir.rglob("*.rpym"))):
                parts = f.relative_to(extract_dir).parts[1:]  # 去掉封包名层
                if not parts:
                    continue
                dst = game_dir_p.joinpath(*parts)
                if not dst.exists():      # 已有真实源码的永不覆盖
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    _sh_dec.move(str(f), str(dst))
        p.emit("decompile", f"反编译完成：{dec_stats['decompiled']} 个成功，"
                            f"{len(dec_stats['failed'])} 个失败，"
                            f"{dec_stats['skipped']} 个已有源码跳过")

    # --- 第 3.5 步：rpy 源码检测（带源码发布，或刚反编译出来） ---
    # 有源码 = 可以安全地改引用、换格式；引擎下次启动会自动重编译
    rpy_files = list(game_dir_p.rglob("*.rpy")) if game_dir_p.is_dir() else []
    has_rpy = bool(rpy_files)
    ref_index = None
    rename_map: dict[str, str] = {}
    if has_rpy:
        p.emit("analyze", f"检测到成品里带有 {len(rpy_files)} 个 .rpy 源码，"
                          "已解锁格式转换能力")
        ref_index = RefIndex(str(game_dir_p))
    if dec_stats is not None:
        report.warnings.append(
            "已启用实验性功能：反编译了编译脚本以解锁格式转换，转换后的资源"
            "已按原样包回封包。反编译产物不保证与原代码 100% 等价，"
            "请务必在处理完后启动游戏确认正常；若有异常，关掉该选项重跑即可。")
        if dec_stats["failed"]:
            report.warnings.append(
                f"{len(dec_stats['failed'])} 个脚本反编译失败（可能混淆或未知语法），"
                "它们引用的资源已按保守策略同名处理。")

    saved = 0
    rpa_replacements: dict[str, dict[str, str]] = {}   # rpa文件名 -> {内部路径: 优化后文件}
    # 第二波：skipped 与 failed 分桶，口径同工程模式
    failed = {"image": 0, "audio": 0, "video": 0, "font": 0}
    skipped = {"image": 0, "audio": 0, "video": 0, "font": 0}
    remap_map: dict[str, str] = {}   # 实验性运行时重映射（BACKLOG B9）
    if options.do_videos and not find_ffmpeg():
        report.warnings.append("未找到 FFmpeg，视频压缩已跳过。")
    if options.do_videos and options.experimental_av1 and find_ffmpeg():
        report.warnings.append(
            "已启用 AV1 视频编码（实验性）：官方支持且体积更小，"
            "但只有 Ren'Py 8.0 及以上构建的游戏能播放，老引擎会放不出来。")

    # --- 第 4 步：优化（默认同名同格式；带源码的成品可换格式；B4 并行） ---
    def _dist_job(a):
        def job():
            out = {"records": [], "saved": 0, "rename": None, "remap": None,
                   "rpa": None, "failed": None, "skipped": None, "warn": None}
            # 脚本引用用的是相对 game/ 的路径，换算一下再查
            try:
                game_rel = Path(a.rel).relative_to("game").as_posix()
            except ValueError:
                game_rel = a.rel
            if a.kind == AssetKind.IMAGE:
                if a.size < preset.min_size_kb * 1024 or a.ext in (".gif", ".bmp", ".avif"):
                    return out
                # 实验性深度压缩：有损量化（同名同格式，安全）
                if (options.png_quant and a.ext == ".png"
                        and a.size >= 64 * 1024 and not a.in_rpa):
                    res = quantize_png(a.path, a.path)
                    if res:
                        out["saved"] = res["old_size"] - res["new_size"]
                        out["records"].append(ChangeRecord(
                            action="quantize", src=a.rel,
                            detail=f"{_fmt(res['old_size'])} -> {_fmt(res['new_size'])}（有损量化）"))
                        return out
                # 实验性运行时重映射：无源码也能转 WebP（注入映射脚本）；
                # remap_usable 是预检后的总开关（第二波接线：不支持/旧脚本坏时
                # 降级走下方同名压缩，复用既有降级路径）
                if (remap_usable and not a.in_rpa
                        and preset.png_to_webp
                        and a.ext in (".png", ".jpg", ".jpeg")
                        # 审核修复：目标撞名时不转（防互覆/映射冲突）
                        and str(Path(a.rel).with_suffix(".webp")).replace("\\", "/")
                        not in clash_webp):
                    new_rel = str(Path(game_rel).with_suffix(".webp")).replace("\\", "/")
                    new_path = str(game_dir_p / new_rel)
                    if Path(new_path).exists():
                        # 审核修复（高-3）：目标已存在绝不覆写，落同名压缩
                        out["warn"] = (f"{a.rel}：换格式目标 {new_rel} 已存在，"
                                       "为避免覆盖已降级为同名压缩。")
                    else:
                        res = optimize_image(a.path, new_path, preset.image_quality,
                                             convert_webp=True)
                        if res["status"] == "ok":
                            Path(a.path).unlink()
                            out["remap"] = (game_rel, new_rel)
                            out["saved"] = res["old_size"] - res["new_size"]
                            out["records"].append(ChangeRecord(
                                action="convert", src=game_rel, dst=new_rel,
                                detail=f"{_fmt(res['old_size'])} -> {_fmt(res['new_size'])}（运行时重映射）"))
                            return out
                        # 转换失败落到同名压缩
                # 带源码 + 查得到引用：可以转 WebP（反编译解锁后也
                # 含封包内资源，转换产物按原样包回 rpa，用户拍板 2026-08-17）
                clash_key = (("game/" + game_rel) if a.in_rpa else a.rel
                             ).replace("\\", "/")
                if (ref_index is not None
                        and preset.png_to_webp
                        and a.ext in (".png", ".jpg", ".jpeg")
                        and str(Path(clash_key).with_suffix(".webp"))
                        not in clash_webp
                        and ref_index.find(game_rel)):
                    new_rel = str(Path(game_rel).with_suffix(".webp")).replace("\\", "/")
                    new_path = str(game_dir_p / new_rel)
                    if Path(new_path).exists():
                        # 审核修复（高-3）：目标已存在绝不覆写，落同名压缩
                        out["warn"] = (f"{a.rel}：换格式目标 {new_rel} 已存在，"
                                       "为避免覆盖已降级为同名压缩。")
                    else:
                        res = optimize_image(a.path, new_path, preset.image_quality,
                                             convert_webp=True)
                        if res["status"] == "ok":
                            if not a.in_rpa:
                                Path(a.path).unlink()
                            out["rename"] = (game_rel, new_rel)
                            out["saved"] = res["old_size"] - res["new_size"]
                            out["records"].append(ChangeRecord(
                                action="convert", src=game_rel, dst=new_rel,
                                detail=f"{_fmt(res['old_size'])} -> {_fmt(res['new_size'])}"
                                       + ("（包回 rpa）" if a.in_rpa else "")))
                            if a.in_rpa:
                                # 改名替换重建：旧条目剔除、新名入包
                                out["rpa"] = (a.rpa_name, a.rel,
                                              (new_rel, new_path))
                        elif res["status"] == "skipped":
                            out["skipped"] = "image"
                        else:
                            out["failed"] = "image"
                else:
                    # 同名压缩（带增量缓存）
                    h = cache.hash_file(a.path) if options.use_cache else None
                    key = f"img|q{preset.image_quality}|same"
                    hit = cache.lookup_hash(h, key) if h else None
                    if hit and cache.apply_cached(hit, a.path):
                        new_sz = Path(a.path).stat().st_size
                        if new_sz < a.size:
                            out["saved"] = a.size - new_sz
                            out["records"].append(ChangeRecord(
                                action="compress", src=a.rel,
                                detail="同名压缩（缓存命中）" + (f"（{a.rpa_name}）" if a.in_rpa else "")))
                            if a.in_rpa:
                                out["rpa"] = (a.rpa_name, a.rel, a.path)
                        return out
                    res = optimize_image(a.path, a.path, preset.image_quality,
                                         convert_webp=False)
                    if res["status"] == "ok":
                        if h:
                            cache.store_hash(h, key, a.path)
                        # 审核修复（中-10）：登记产物自映射，防 in_place
                        # 反复运行时有损重编码代际累积
                        if options.use_cache:
                            cache.store_self(a.path, key)
                        out["saved"] = res["old_size"] - res["new_size"]
                        out["records"].append(ChangeRecord(
                            action="compress", src=a.rel,
                            detail="同名压缩" + (f"（{a.rpa_name}）" if a.in_rpa else "")))
                        if a.in_rpa:
                            out["rpa"] = (a.rpa_name, a.rel, a.path)
                    elif res["status"] == "skipped":
                        out["skipped"] = "image"
                    else:
                        out["failed"] = "image"
            elif a.kind == AssetKind.AUDIO:
                # 带源码 + 查得到引用：WAV/MP3 可以转 OGG（反编译解锁后
                # 也含封包内资源，转换产物按原样包回 rpa）
                clash_key = (("game/" + game_rel) if a.in_rpa else a.rel
                             ).replace("\\", "/")
                if (ref_index is not None
                        and a.ext in (".wav", ".mp3")
                        and str(Path(clash_key).with_suffix(".ogg"))
                        not in clash_ogg
                        and ref_index.find(game_rel)):
                    new_rel = str(Path(game_rel).with_suffix(".ogg")).replace("\\", "/")
                    new_path = str(game_dir_p / new_rel)
                    if Path(new_path).exists():
                        # 审核修复（高-3）：目标已存在（如 wav 源与 ogg 发布版
                        # 并存），绝不覆写，跳过格式转换
                        out["warn"] = (f"{a.rel}：换格式目标 {new_rel} 已存在，"
                                       "为避免覆盖已跳过格式转换。")
                    else:
                        res = convert_audio(a.path, new_path, preset.audio_bitrate_k)
                        if res["status"] == "ok":
                            if not a.in_rpa:
                                Path(a.path).unlink()
                            out["rename"] = (game_rel, new_rel)
                            out["saved"] = res["old_size"] - res["new_size"]
                            out["records"].append(ChangeRecord(
                                action="convert", src=game_rel, dst=new_rel,
                                detail=f"{_fmt(res['old_size'])} -> {_fmt(res['new_size'])}"
                                       + ("（包回 rpa）" if a.in_rpa else "")))
                            if a.in_rpa:
                                out["rpa"] = (a.rpa_name, a.rel,
                                              (new_rel, new_path))
                        elif res["status"] == "skipped":
                            out["skipped"] = "audio"
                        else:
                            out["failed"] = "audio"
                elif a.ext in (".ogg", ".mp3") and a.bitrate and \
                        a.bitrate > preset.audio_bitrate_k + 32:
                    # 同名同格式重编码，成品模式下安全（带增量缓存）
                    h = cache.hash_file(a.path) if options.use_cache else None
                    key = f"aud|{preset.audio_bitrate_k}|re-{a.ext[1:]}"
                    hit = cache.lookup_hash(h, key) if h else None
                    if hit and cache.apply_cached(hit, a.path):
                        new_sz = Path(a.path).stat().st_size
                        if new_sz < a.size:
                            out["saved"] = a.size - new_sz
                            out["records"].append(ChangeRecord(
                                action="compress", src=a.rel,
                                detail=f"{a.ext[1:].upper()} 降码率（缓存命中）"))
                            if a.in_rpa:
                                out["rpa"] = (a.rpa_name, a.rel, a.path)
                        return out
                    res = reencode_audio(a.path, a.path, preset.audio_bitrate_k)
                    if res["status"] == "ok":
                        if h:
                            cache.store_hash(h, key, a.path)
                        # 审核修复（中-10）：同上，防重编码代际累积
                        if options.use_cache:
                            cache.store_self(a.path, key)
                        out["saved"] = res["old_size"] - res["new_size"]
                        out["records"].append(ChangeRecord(
                            action="compress", src=a.rel,
                            detail=f"{a.ext[1:].upper()} 降码率（保持格式）"))
                        if a.in_rpa:
                            out["rpa"] = (a.rpa_name, a.rel, a.path)
                    elif res["status"] == "skipped":
                        out["skipped"] = "audio"
                    else:
                        out["failed"] = "audio"
                elif a.ext == ".wav":
                    if ref_index is not None:
                        out["warn"] = (f"{a.rel}：成品带源码但没查到它的字面引用，"
                                       "可能被变量拼接调用，为安全起见不换格式。")
                    else:
                        out["warn"] = (f"{a.rel}：成品内的 WAV 不能换格式（引用被焊死），"
                                       "无法安全压缩。")
            elif a.kind == AssetKind.VIDEO:
                # 视频压缩（BACKLOG B7，实验性，默认关）：同名同格式重编码
                if options.do_videos and a.ext in (".mp4", ".webm", ".ogv") and find_ffmpeg():
                    try:
                        res = compress_video(a.path, a.path, options.preset,
                                             use_av1=options.experimental_av1)
                    except RuntimeError as e:
                        res = None
                        out["warn"] = str(e)
                    if res is not None and res["status"] == "ok":
                        out["saved"] = res["old_size"] - res["new_size"]
                        out["records"].append(ChangeRecord(
                            action="compress", src=a.rel,
                            detail=f"视频重编码 {_fmt(res['old_size'])} -> {_fmt(res['new_size'])}"))
                        if a.in_rpa:
                            out["rpa"] = (a.rpa_name, a.rel, a.path)
                    elif res is not None:
                        if res["status"] == "skipped":
                            out["skipped"] = "video"
                            if res.get("reason"):
                                out["warn"] = f"{a.rel}：{res['reason']}"
                        else:
                            out["failed"] = "video"
                            if res.get("reason"):
                                out["warn"] = f"{a.rel}：视频压缩失败（{res['reason']}）"
            elif a.kind == AssetKind.FONT and a.ext in (".ttf", ".otf"):
                if a.size < 256 * 1024:
                    return out
                try:
                    res = subset_font(a.path, a.path, chars)
                    out["saved"] = res["old_size"] - res["new_size"]
                    out["records"].append(ChangeRecord(
                        action="subset_font", src=a.rel,
                        detail=f"字形 {res['glyphs_before']} -> {res['glyphs_after']}"))
                    if a.in_rpa:
                        out["rpa"] = (a.rpa_name, a.rel, a.path)
                except Exception as e:
                    out["failed"] = "font"
                    out["warn"] = f"{a.rel}：字体瘦身失败，保留原文件（{e}）"
            return out
        return a.rel, job

    # 审核修复：换后缀转换的同名撞车预检（同工程模式）
    # 审核修复（中-33）：参照集合并入封包内全部资源名——散文件加载
    # 优先级高于封包，转换产物若与封包内资源同名会静默遮蔽它
    def _working_rel(a):
        # 统一到"相对工作目录"坐标系：封包内资源的 rel 是相对
        # game/ 的，物理位置在 game/ 下，需补前缀
        rel = a.rel.replace("\\", "/")
        return f"game/{rel}" if a.in_rpa else rel

    all_rels = [_working_rel(a) for a in all_assets]
    # 反编译解锁后封包内资源也能转换，撞名预检源集不再排除 in_rpa
    clash_webp = find_suffix_clashes(
        [_working_rel(a) for a in all_assets if a.kind == AssetKind.IMAGE
         and a.ext in (".png", ".jpg", ".jpeg")], ".webp",
        existing=all_rels)
    clash_ogg = find_suffix_clashes(
        [_working_rel(a) for a in all_assets if a.kind == AssetKind.AUDIO
         and a.ext in (".wav", ".mp3")], ".ogg",
        existing=all_rels)

    # 审核修复（中-2）：音频补 FFmpeg 预检（以前只有视频查），
    # 否则缺 FFmpeg 时音频优化被静默吞掉，用户完全不知情
    if options.do_audio and not find_ffmpeg():
        report.warnings.append("未找到 FFmpeg，音频优化已跳过。")

    # --- remap 预检（第二波接线）---
    # 实验性运行时重映射必须在任务执行前通过两道预检，否则整批降级为
    # 同名压缩（复用既有降级路径）。转换发生在 job 里、改名后无法挽回，
    # 所以预检必须放在构建任务之前：
    # ① 引擎支持（script_version ≥ 8.0.0 且无回调冲突）；
    # ② 已有注入脚本（若存在）必须仍可解析，否则合并会丢旧映射。
    remap_usable = bool(options.experimental_remap)
    if remap_usable:
        _ok_support, _reason = remap_mod.check_remap_support(game_dir_p)
        if not _ok_support:
            remap_usable = False
            report.warnings.append(
                f"运行时重映射暂不可用（{_reason}），"
                "本次图片改走安全的同名压缩。")
    if remap_usable:
        _pre_script = game_dir_p / remap_mod.REMAP_SCRIPT_NAME
        if _pre_script.exists():
            try:
                _pre_old, _pre_ok = remap_mod.parse_remap_mapping(
                    _pre_script.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                _pre_old, _pre_ok = {}, False
            if not _pre_ok:
                remap_usable = False
                report.warnings.append(
                    "旧映射解析失败，为避免丢失映射已跳过注入，"
                    "请检查 rtools_remap.rpy。本次图片改走安全的同名压缩。")
            del _pre_old

    _DIST_KIND = {AssetKind.IMAGE: "image", AssetKind.AUDIO: "audio",
                  AssetKind.VIDEO: "video", AssetKind.FONT: "font"}
    dist_jobs = []
    for a in all_assets:
        # 引擎自带目录（renpy/common 等）里的资源不碰：
        # 内置界面/报错文本用的字符和文件扫描不到，瘦身会出事
        top_dir = a.rel.replace("\\", "/").split("/")[0].lower()
        if top_dir in ("renpy", "lib"):
            continue
        # 审核修复（中-7）：_rtools_extract 是解包工作区（上次运行
        # 残留时会被扫进来），绝不参与优化
        if top_dir == "_rtools_extract":
            continue
        # 审核修复（严重-1）：用户开关在成品模式也必须被尊重——
        # 此前 do_images/do_audio/do_fonts 在成品模式全部失效，
        # 用户关掉字体瘦身字体照样被不可逆剃掉
        if a.kind == AssetKind.IMAGE and not options.do_images:
            continue
        if a.kind == AssetKind.AUDIO and not options.do_audio:
            continue
        if a.kind == AssetKind.FONT and not (options.do_fonts
                                             and preset.font_subset):
            continue
        if a.kind == AssetKind.VIDEO and not options.do_videos:
            continue
        # 第二波：_safe_job 包装兜底异常并按资源类型归因计 failed；
        # 记账以返回 dict 的 failed/skipped 字段为准
        dist_jobs.append(_safe_job(_DIST_KIND.get(a.kind, "image"),
                                   _dist_job(a)))

    for r in _run_jobs_or_flush(p, "optimize", dist_jobs, cancel,
                                output_dir, records, saved):
        records.extend(r["records"])
        saved += r["saved"]
        if r["rename"]:
            rename_map[r["rename"][0]] = r["rename"][1]
        if r["remap"]:
            remap_map[r["remap"][0]] = r["remap"][1]
        if r["rpa"]:
            rpa_replacements.setdefault(r["rpa"][0], {})[r["rpa"][1]] = r["rpa"][2]
        # 记账以字段为准（第二波）：failed/skipped 均为类型名字符串；
        # isinstance 防御兜底记录（极端路径下可能不是字符串），
        # 绝不让记账环节抛 KeyError 把整批成果带崩。
        fk = r.get("failed")
        if isinstance(fk, str):
            if fk in failed:
                failed[fk] += 1
            else:
                # 收口修复：兜底归因 "internal" 等非类型名失败也计数，
                # 不再被 isinstance 过滤静默吞掉（失败数虚低）。
                failed[fk] = failed.get(fk, 0) + 1
        sk = r.get("skipped")
        if isinstance(sk, str) and sk in skipped:
            skipped[sk] += 1
        if r["warn"]:
            report.warnings.append(r["warn"])

    # --- 第 4.3 步：实验性运行时重映射脚本注入（BACKLOG B9） ---
    # 审核修复（高-5）：从这里开始每一步都可能改盘（remap 注入/引用改写/
    # 封包重建/隔离/写报告），任何异常都要先把已发生的改动落清单
    try:
        _check_cancel(cancel)
        if remap_map:
            script_path = game_dir_p / remap_mod.REMAP_SCRIPT_NAME
            # 审核修复：若上次运行已注入过脚本，先读回旧映射再合并——
            # 直接覆写会丢旧条目，而旧条目对应的原文件已被删除，
            # 丢了映射 = 那些图再也加载不到（坏图）
            merged = dict(remap_map)
            inject = True
            if script_path.exists():
                try:
                    # 第二波接线：parse 现返回 (dict, bool) 元组
                    old_map, ok = remap_mod.parse_remap_mapping(
                        script_path.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    old_map, ok = {}, False
                if not ok:
                    # 收口修复：此时转换已全部完成（图片原件已删、
                    # WebP 已就位），跳过注入 = 这批图失去运行时映射，
                    # 产物不可发布。不再静默降级（"改走同名压缩"已不成立），
                    # 明确报警并按既有口径计入失败；不回退文件（复杂度不可控）。
                    inject = False
                    report.warnings.append(
                        "remap 映射写入失败：本次已转换的 "
                        f"{len(remap_map)} 张图片映射未写入，请勿发布该产物。"
                        f"（已有注入脚本 {remap_mod.REMAP_SCRIPT_NAME} "
                        "解析失败，为避免丢失旧映射中止了合并写入）")
                    failed["image"] = failed.get("image", 0) + len(remap_map)
                else:
                    merged = {**old_map, **remap_map}
            if inject:
                script_path.write_text(remap_mod.build_remap_script(merged),
                                       encoding="utf-8")
                records.append(ChangeRecord(
                    action="remap_inject", src=remap_mod.REMAP_SCRIPT_NAME,
                    detail=f"{len(remap_map)} 个图片请求将在运行时被透明重定向（实验性功能）"))
                report.warnings.append(
                    "已启用实验性功能：注入了运行时重映射脚本 game/"
                    f"{remap_mod.REMAP_SCRIPT_NAME}。若游戏出现异常，删掉该文件即可完全还原。")

        # 失败口径诚实化（BACKLOG B3）；第二波：failed/skipped 分桶，
        # 口径与工程模式一致——失败与“没变小/格式不适合”分开报。
        total_failed = sum(failed.values())
        if total_failed:
            report.warnings.append(
                f"{total_failed} 个文件处理失败（详见日志），"
                "已原样保留，未计入节省体积。")
        total_skipped = sum(skipped.values())
        if total_skipped:
            report.warnings.append(
                f"{total_skipped} 个文件处理后未能进一步压缩"
                "（可能已是最优或格式不适合），已原样保留，未计入节省体积。")

        # --- 第 4.5 步：带源码的成品，改写脚本引用 ---
        _check_cancel(cancel)
        if rename_map and ref_index is not None:
            p.emit("rewrite", f"正在改写 {len(rename_map)} 个资源的脚本引用……")
            records.extend(ref_index.rewrite(rename_map))

        # --- 第 5 步：重建含优化内容的 RPA 封包 ---
        for rpa_name, repl in rpa_replacements.items():
            _check_cancel(cancel)
            rpa_files = list(working_p.rglob(rpa_name))
            if not rpa_files:
                continue
            # 第二波：同名封包多份时不能盲取第一个重建——另外几份里的
            # 同名条目仍旧，会产生不一致的兄弟副本；跳过重建并警告。
            if len(rpa_files) > 1:
                report.warnings.append(
                    f"{rpa_name}：在工作副本里找到 {len(rpa_files)} 份同名封包，"
                    "无法确定该重建哪一份，已跳过该批重建并保留原包。"
                    "优化后的文件已按散落副本保留，游戏仍可加载。")
                continue
            src_rpa = rpa_files[0]
            p.emit("rpa", f"正在重建封包 {rpa_name}（替换 {len(repl)} 个文件）……")
            tmp_rpa = src_rpa.with_name(src_rpa.name + ".rtools.tmp")
            try:
                replaced, total = rpa.rebuild_archive(str(src_rpa), str(tmp_rpa), repl)
                old_size, new_size = src_rpa.stat().st_size, tmp_rpa.stat().st_size
                tmp_rpa.replace(src_rpa)
                # 改名替换（转换后包回 rpa）的散落副本已入包，删掉避免
                # 双份存在；若重建失败则散落副本保留——引用已改写，
                # 散文件照样能加载，是天然兜底
                for v in repl.values():
                    if isinstance(v, tuple):
                        Path(v[1]).unlink(missing_ok=True)
                records.append(ChangeRecord(
                    action="rpa_rebuild", src=rpa_name,
                    detail=f"封包内 {total} 个文件，替换 {replaced} 个，"
                           f"{_fmt(old_size)} -> {_fmt(new_size)}"))
            except Exception as e:
                tmp_rpa.unlink(missing_ok=True)
                report.warnings.append(f"{rpa_name}：封包重建失败，保留原封包（{e}）")

        # --- 第 6 步：无引用冗余文件（默认只标记，不删除） ---
        unreferenced = _find_unreferenced(working_p, loose)
        if unreferenced:
            report.warnings.append(
                f"发现 {len(unreferenced)} 个疑似无任何引用的文件（见 analysis.json "
                f"unreferenced 字段）。默认不删除；如需清理请在选项里开启。")
        if options.delete_unreferenced and unreferenced:
            quarantine = working_p / "_rtools_quarantine"
            for rel in unreferenced:
                src = working_p / rel
                # 审核修复（高-5）：文件可能已被前序步骤动过，逐项防御
                if not src.exists():
                    continue
                dst = quarantine / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    src.rename(dst)
                except OSError:
                    continue
                records.append(ChangeRecord(action="quarantine", src=rel,
                                            dst=str(dst), detail="疑似无引用，已隔离而非删除"))

        # --- 第 6.5 步：清掉成品里带的可再生垃圾（真实样本发现：发布包竞带着 cache） ---
        # 审核修复：in_place 时绝不能清——JUNK_DIRS 含 saves，而玩家存档不可再生；
        # 与工程模式（第 5.5 步）保持一致的保护策略。
        junk = {"freed_bytes": 0, "removed": []}
        if options.in_place:
            report.warnings.append(
                "直接修改原件模式下跳过了垃圾清理（为保护你的存档和缓存）。")
        else:
            junk = cleanup.clean_junk(working)
            if junk["removed"]:
                saved += junk["freed_bytes"]
                records.append(ChangeRecord(
                    action="junk_clean", src=working,
                    detail=f"清理 {len(junk['removed'])} 项可再生垃圾，"
                           f"释放 {_fmt(junk['freed_bytes'])}"))

        # --- 第 7 步：报告 ---
        _check_cancel(cancel)
        out = Path(output_dir)
        d = report.to_dict()
        d["unreferenced"] = unreferenced
        _write_json(out / "analysis.json", d)
        _write_json(out / "changelog.json",
                    {"records": [r.to_dict() for r in records], "saved_bytes": saved})
    except BaseException:
        # 审核修复（高-5）：任何异常（含取消）都落部分清单，
        # 磁盘已被部分修改时用户有对账依据
        _flush_partial_changelog(output_dir, records, saved)
        raise
    finally:
        # 审核修复（中-7）：解包临时目录任何路径上都不能残留，
        # in_place 时它就在用户原成品目录里，残留会被下次运行当资源处理
        import shutil as _sh
        if extract_dir.exists():
            _sh.rmtree(extract_dir, ignore_errors=True)

    p.emit("done", f"成品瘦身完成，共节省约 {_fmt(saved)}")
    return {
        "mode": "dist",
        "working_dir": working,
        "saved_bytes": saved,
        "has_rpy": has_rpy,
        "report": str(out / "analysis.json"),
        "changelog": str(out / "changelog.json"),
        "warnings": report.warnings,
        "unreferenced": unreferenced,
        "failed": failed,
        "skipped": skipped,
        "remapped": len(remap_map),
        "junk": junk,
        "report_dict": d,
    }


# ===========================================================================
# 模式 B 自动流：压缩包直接进，瘦身后再打包成压缩包出
# ===========================================================================

def run_dist_smart(path: str, options: OptimizeOptions,
                   work_root: str, output_dir: str,
                   progress: Progress | None = None,
                   password: str | None = None,
                   cancel: Callable[[], bool] | None = None) -> dict:
    """输入可以是成品目录，也可以是 zip/7z/rar 压缩包。

    压缩包输入时：解压 -> 定位成品目录 -> 瘦身 -> 重新打包成
    "原名-瘦身版.zip"（放在输出目录）。目录输入时行为与 run_dist 一致。
    """
    p = progress or Progress()
    if not archives.is_archive(path):
        # 审核修复：目录分支也得把取消开关传下去，否则取消按钮失灵
        return run_dist(path, options, work_root, output_dir, p,
                        cancel=cancel)

    src_name = Path(path).stem
    extract_dir = str(Path(work_root) / f"{src_name}-解压")
    # 第二波：解压前先删除上次运行残留的同名目录——上次若崩溃在清理前，
    # 残留会被本次扫描混入（重复记账/误打包）；删不掉时改用唯一后缀备用目录，
    # 绝不在脏目录上继续。
    if Path(extract_dir).exists():
        try:
            shutil.rmtree(extract_dir, ignore_errors=False)
        except OSError:
            p.emit("unpack",
                   f"警告：无法删除残留解压目录 {Path(extract_dir).name}"
                   "（可能被其他程序占用），已改用新临时目录继续。")
            extract_dir = str(Path(work_root)
                              / f"{src_name}-解压-{uuid.uuid4().hex[:8]}")
    p.emit("unpack", f"正在解压压缩包 {Path(path).name}……")
    archives.extract_archive(path, extract_dir, password)
    try:
        # 审核修复（高-4）：取全部候选成品根——旧版只拿第一个，
        # 多平台发布包（PC+Mac 三合一等）里其余平台被静默丢弃
        dist_roots = archives.find_dist_roots(extract_dir)
    except archives.ArchiveError:
        # F8：压缩包里装的是 APK 而不是成品目录 → 自动转入 APK 安全瘦身。
        # 走保守的同名压缩档（不换格式不签名，不引入运行时钩子）；
        # 想要全力瘦身/签名请用专门的 APK 瘦身入口直接选这个 APK。
        apk_files = [f for f in Path(extract_dir).rglob("*")
                     if f.is_file() and f.suffix.lower() == ".apk"]
        if not apk_files:
            raise
        src_apk = sorted(apk_files, key=lambda f: len(f.parts))[0]
        p.emit("apk", f"压缩包里装的是 APK，自动转入 APK 瘦身：{src_apk.name}")
        # F8 设计：走保守的同名压缩档（不换格式不签名）——与注释名实相符
        r = apk.slim_apk(str(src_apk), "conservative", progress=p)
        r = dict(r)
        # 审核修复（中-5）：产物不能埋在临时解压目录深处，挪到输出目录
        try:
            import shutil as _sh_apk
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            moved = Path(output_dir) / Path(r["output"]).name
            _sh_apk.move(str(r["output"]), str(moved))
            r["output"] = str(moved)
        except OSError:
            pass
        r["mode"] = "apk"
        r["archive_input"] = path
        r.setdefault("warnings", []).append(
            "压缩包内是 APK：已按安全档瘦身（同名压缩、不换格式、未签名）。"
            "如需最大瘦身（图转 WebP/音转 OGG）或签名安装，"
            "请改用“APK 瘦身”页面直接选择这个 APK。")
        # 第二波：APK 转入分支结束前也清掉解压目录（产物已挪到输出目录）
        shutil.rmtree(extract_dir, ignore_errors=True)
        return r
    if len(dist_roots) > 1:
        names = "；".join(Path(x).name for x in dist_roots)
        raise PipelineError(
            f"压缩包里找到 {len(dist_roots)} 个成品目录（{names}）。"
            "本工具一次只处理一个成品，请把各平台拆开分别瘦身——"
            "否则只会输出其中一个平台，其余内容会丢失。")
    dist_root = dist_roots[0]
    p.emit("unpack", f"已定位成品目录：{Path(dist_root).name}")

    result = run_dist(dist_root, options, work_root, output_dir, p,
                      cancel=cancel)

    # 第二波：run_dist 之后的收尾步骤（改名/回包）同样尊重取消
    _check_cancel(cancel)

    # 交付包里的文件夹用原成品目录名，不带时间戳，好认又整洁
    import shutil as _shutil
    proper_name = Path(dist_root).name
    wd = Path(result["working_dir"])
    if wd.name != proper_name:
        target = wd.parent / proper_name
        if target.exists():
            _shutil.rmtree(target, ignore_errors=True)
            if target.exists():
                # 第二波：rmtree 静默失败时先检测残留——旧目标删不掉就绝不能
                # 硬改名（会直接报 FileExistsError），给明确错误提示。
                raise PipelineError(
                    f"无法删除旧的目标目录 {target.name}"
                    "（可能被其他程序占用），请关闭相关程序后重试。")
        try:
            wd.rename(target)
        except OSError as e:
            raise PipelineError(
                f"无法把工作目录改名为 {target.name}"
                f"（目标目录可能被其他程序占用）：{e}") from e
        result["working_dir"] = str(target)

    # 瘦身后的成品重新打包；输出 zip 用原始压缩包名，好认
    _check_cancel(cancel)
    p.emit("repack", "正在把瘦身成品重新打包成 zip……")
    out_zip = str(Path(output_dir) / f"{src_name}-瘦身版.zip")
    archives.create_zip(result["working_dir"], out_zip)
    result["archive_output"] = out_zip
    result["archive_input"] = path
    # 审核修复（中-6）：回包成功后解压目录不再需要——不清理的话
    # _rtools_work 每次运行都会累积一份全量解压拷贝
    _shutil.rmtree(extract_dir, ignore_errors=True)
    p.emit("done", f"交付包已生成：{out_zip}")
    return result


def _find_unreferenced(working_p: Path, loose: list) -> list[str]:
    """在所有编译脚本的原始字节里查找文件名，查不到的视为疑似无引用。

    保守策略，四重排除：
    - 只针对 game/ 目录内的资源（引擎运行时文件一律不碰）；
    - 字体永不标记（常由配置动态指定，误删后果严重）；
    - 图片永不标记（GUI 系统按目录约定动态加载，字面搜索假阳性极高）；
    - 名字太短的不标记（容易误判）。
    因此实际只标记音频/视频类文件，且仅作为报告提示。
    """
    blobs: list[bytes] = []
    for pat in ("*.rpyc", "*.rpymc"):
        for p in working_p.rglob(pat):
            # .rpyc 是 zlib 压缩存储，必须先解压才能搜到文本
            text = charset.read_rpyc_text(p)
            if text:
                blobs.append(text.encode("utf-8", "ignore"))
    if not blobs:
        return []

    result = []
    for a in loose:
        rel = a.rel.replace("\\", "/")
        if a.kind in (AssetKind.FONT, AssetKind.IMAGE):
            continue
        top = rel.split("/")[0].lower()
        if top != "game":        # renpy/、lib/ 等引擎目录一律不动
            continue
        name = Path(rel).name.encode("utf-8", "ignore")
        if len(name) < 5:
            continue
        if not any(name in b for b in blobs):
            result.append(rel)
    return result
