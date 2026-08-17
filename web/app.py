"""Web 服务：FastAPI 应用，图形界面入口的后端。

任务在后台线程执行，前端通过轮询 /api/job/{id} 获取进度与日志。
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections import deque
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from rtools import analyzer, apk, archives, charset, font_tool, packager, pipeline, scanner
from rtools.config import (CharsetOptions, OptimizeOptions, PRESETS,
                           DEFAULT_PRESET, default_options)
from rtools.models import Progress

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Ren'Py 工具箱")

# ---------------------------------------------------------------------------
# 本地专用防护：只认本机来源的请求
# ---------------------------------------------------------------------------
# 服务已绑定 127.0.0.1，但还要防“域名重绑定”类手法：恶意网页
# 让自己的域名解析到本机，借浏览器之手指挥本地服务。对策是
# 核对请求的“门牌号”：Host 不是本机地址、或 Origin 来自别的
# 网站，一律拒收。正常自用完全无感。
_ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


@app.middleware("http")
async def guard_local_only(request: Request, call_next):
    host = request.headers.get("host", "").split(":")[0].lower()
    if host and host not in _ALLOWED_HOSTS:
        return JSONResponse({"ok": False, "error": "非本机请求，已拒绝"},
                            status_code=403)
    origin = request.headers.get("origin", "")
    if origin:
        from urllib.parse import urlparse
        origin_host = (urlparse(origin).hostname or "").lower()
        if origin_host and origin_host not in _ALLOWED_HOSTS:
            return JSONResponse({"ok": False, "error": "非本机来源，已拒绝"},
                                status_code=403)
    return await call_next(request)

# ---------------------------------------------------------------------------
# 任务管理
# ---------------------------------------------------------------------------

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()

# 任务队列（用户要求 2026-08-17）：重任务（瘦身/打包/APK/字体）同一时刻
# 只跑一个——并发会共用 _rtools_work/_rtools_output，固定名称的产物互相
# 覆盖，还抢 CPU/磁盘。后提交的任务自动排队，前一个结束自动接着跑；
# 只读分析不产出文件，不参与排队，随时可并发跑。
_JOB_TASKS: dict[str, callable] = {}   # 排队中任务的可执行体，开跑时弹出
_QUEUE: deque[str] = deque()           # 排队任务编号，先进先出
_QUEUE_LOCK = threading.Lock()
_RUNNING = {"id": None}                # 当前正在跑的重任务编号


def _new_job(kind: str) -> str:
    job_id = uuid.uuid4().hex[:8]
    with JOBS_LOCK:
        # 清理旧任务，防止长期运行时内存无限增长：
        # 超过 2 小时的直接丢，剩下的只保留最近 30 个
        # 审核修复（高-2）：超时清理必须跳过运行中的任务——
        # 旧版无状态检查，长任务跑超 2 小时后一建新任务就被删，
        # 线程收尾 KeyError、状态永久卡死、还写假崩溃转储
        now = time.time()
        for k in [k for k, v in JOBS.items()
                  if now - v["created"] > 7200 and v["status"] != "running"]:
            del JOBS[k]
        if len(JOBS) > 30:
            for k, _ in sorted(JOBS.items(), key=lambda kv: kv[1]["created"])[:-30]:
                if JOBS[k]["status"] != "running":
                    del JOBS[k]
        JOBS[job_id] = {
            "id": job_id, "kind": kind, "status": "running",
            "logs": [], "result": None, "error": None,
            "cancel": False,
            "created": time.time(),
        }
    return job_id


def _job_cancelled(job_id: str) -> bool:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return bool(job and job["cancel"])


def _job_log(job_id: str, stage: str, message: str) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job:
            job["logs"].append({"t": time.time(), "stage": stage,
                                "message": message})


def _dispatch_job(job_id: str, fn) -> None:
    """提交任务：没有重任务在跑就立即开跑，否则排队等位。

    排队时把可执行体存进 _JOB_TASKS（不放 JOBS：任务字典要原样
    过 JSON 接口，callable 塞不进去）。
    """
    with _QUEUE_LOCK:
        with JOBS_LOCK:
            busy = _RUNNING["id"] and JOBS.get(_RUNNING["id"], {}).get(
                "status") == "running"
        if not busy:
            _RUNNING["id"] = job_id
            _run_in_thread(job_id, fn)
            return
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job["status"] = "queued"
        _JOB_TASKS[job_id] = fn
        _QUEUE.append(job_id)


def _scheduler_loop():
    """后台调度：排队任务一个个接着跑（由 _run_in_thread 收尾时唤醒）。"""
    while True:
        with _QUEUE_LOCK:
            job_id = None
            while _QUEUE:
                cand = _QUEUE.popleft()
                with JOBS_LOCK:
                    job = JOBS.get(cand)
                if job and job["status"] == "queued":
                    job_id = cand
                    break
                _JOB_TASKS.pop(cand, None)   # 排队时被取消的，丢弃
            if not job_id:
                _RUNNING["id"] = None
                return
            _RUNNING["id"] = job_id
            fn = _JOB_TASKS.pop(job_id)
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job and job["status"] == "queued":
                job["status"] = "running"
        _run_in_thread(job_id, fn)


def _notify_job_done(_job_id: str) -> None:
    """任务结束（含排队中途被取消）：有排队的就接着跑，否则收工。"""
    threading.Thread(target=_scheduler_loop, daemon=True).start()


def _run_in_thread(job_id: str, fn) -> None:
    def wrapper():
        # 审核修复（高-2）：kind 在任务还在时先捕获，后面不依赖
        # JOBS[job_id] 下标（任务字典可能被清理删掉）
        with JOBS_LOCK:
            kind = JOBS.get(job_id, {}).get("kind", "job")
        try:
            result = fn()
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job:
                    job["status"] = "done"
                    job["result"] = result
        except pipeline.PipelineCancelled:
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job:
                    job["status"] = "canceled"
                    job["error"] = "用户已取消，已完成的部分成果保留在结果目录。"
        except scanner.ScanCancelled:
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job:
                    job["status"] = "canceled"
                    job["error"] = "用户已取消扫描。"
        except Exception as e:
            from rtools import crashdump
            dump = crashdump.write_crash(kind)
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job:
                    job["status"] = "error"
                    job["error"] = str(e) + (
                        f"\n崩溃详情已存：{dump}" if dump else "")
                    job["logs"].append(
                        {"t": time.time(), "stage": "error",
                         "message": traceback.format_exc(limit=5)})
        finally:
            _notify_job_done(job_id)

    threading.Thread(target=wrapper, daemon=True).start()


def _options_from_dict(d: dict) -> OptimizeOptions:
    opts = default_options()
    opts.preset = d.get("preset") or DEFAULT_PRESET
    opts.do_images = d.get("do_images", True)
    opts.do_audio = d.get("do_audio", True)
    opts.do_fonts = d.get("do_fonts", True)
    opts.convert_png_webp = d.get("convert_png_webp", True)
    opts.in_place = d.get("in_place", False)
    opts.delete_unreferenced = d.get("delete_unreferenced", False)
    opts.quarantine_unused = d.get("quarantine_unused", False)
    opts.png_quant = d.get("png_quant", False)
    opts.experimental_remap = d.get("experimental_remap", False)
    opts.experimental_av1 = d.get("experimental_av1", False)
    opts.experimental_decompile = d.get("experimental_decompile", False)
    opts.do_videos = d.get("do_videos", False)
    opts.use_cache = d.get("use_cache", True)
    cs = CharsetOptions()
    csd = d.get("charset", {})
    cs.base_latin = csd.get("base_latin", True)
    cs.cjk_punct = csd.get("cjk_punct", True)
    cs.fullwidth = csd.get("fullwidth", False)
    cs.kana = csd.get("kana", False)
    cs.extra_chars = csd.get("extra_chars", "")
    opts.charset = cs
    return opts


def _clean_result(result: dict) -> dict:
    result = dict(result)
    result.pop("report_dict", None)
    for k in ("report", "changelog"):
        if k in result:
            result[k] = str(result[k])
    return result


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class AnalyzeReq(BaseModel):
    path: str
    mode: str = "project"
    password: Optional[str] = None


class OptimizeReq(BaseModel):
    path: str
    mode: str = "project"
    work_root: Optional[str] = None
    output: Optional[str] = None
    options: dict = {}
    password: Optional[str] = None


class PackageReq(BaseModel):
    path: str
    platforms: list[str] = ["pc"]
    destination: Optional[str] = None
    sdk: Optional[str] = None
    archive_rpa: bool = False


class FullReq(BaseModel):
    path: str
    platforms: list[str] = ["pc"]
    destination: Optional[str] = None
    sdk: Optional[str] = None
    options: dict = {}
    archive_rpa: bool = False


class FontSlimReq(BaseModel):
    font: str
    sources: list[str]
    charset: dict = {}


class SlimApkReq(BaseModel):
    path: str
    preset: str = DEFAULT_PRESET
    remap: bool = False          # 实验性：图转 WebP/音转 OGG + 注入重映射脚本
    gen_key: bool = False        # 自动生成新钥匙（小白推荐）
    keystore: Optional[str] = None
    ks_pass: Optional[str] = None
    key_alias: Optional[str] = None


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------

@app.get("/api/env")
def api_env():
    import rtools
    return {"ok": True, "version": rtools.__version__,
            "environment": packager.check_environment(),
            "presets": {k: v.label for k, v in PRESETS.items()}}


def _detect_mode(path: Path) -> str:
    """工程有 .rpy 源码脚本，成品只有编译后的 .rpyc，以此区分。"""
    game = path / "game"
    if game.is_dir() and any(game.rglob("*.rpy")):
        return "project"
    return "dist"


@app.post("/api/analyze")
def api_analyze(req: AnalyzeReq):
    path = Path(req.path)
    if not path.exists():
        return JSONResponse({"ok": False, "error": f"路径不存在：{req.path}"})

    # 分析改后台任务跑：扫描大项目耗时不短，前端轮询实时进度
    job_id = _new_job("analyze")

    def task():
        import shutil
        import tempfile
        p = path
        scan_log = lambda i, total, name: _job_log(
            job_id, "scan", f"扫描资源 {i}/{total}：{name}")
        cleanup_dir = None
        try:
            # 压缩包直接进：解压到临时目录，分析完清理
            is_archive = archives.is_archive(str(p))
            if is_archive:
                _job_log(job_id, "unpack", f"正在解压压缩包 {p.name}…")
                cleanup_dir = tempfile.mkdtemp(prefix="rtools_unpack_")
                archives.extract_archive(str(p), cleanup_dir, req.password)
                p = Path(archives.find_dist_root(cleanup_dir))
                _job_log(job_id, "unpack", f"已定位成品目录：{p.name}")

            # 审核修复（中-31）：压缩包输入强制走 dist 分析——
            # 旧版沿用页面传来的 mode，project 页手输 zip 路径会报
            # "缺少 game 目录"的误导错
            mode = _detect_mode(p) if is_archive else (req.mode or _detect_mode(p))

            if mode == "project":
                if not (p / "game").is_dir():
                    raise ValueError(f"{req.path} 缺少 game 目录，不是有效工程")
                game = str(p / "game")
                assets = scanner.scan_assets(
                    game, probe=True, progress=scan_log,
                    cancel=lambda: _job_cancelled(job_id))
                report = analyzer.analyze(assets, game, "project")
                _job_log(job_id, "analyze", "正在提取字符集…")
                chars, warns = charset.extract_charset(game, CharsetOptions())
                report.warnings.extend(warns)
                report.charset_size = len(chars)
                charlist_str = "".join(sorted(c for c in chars if c.isprintable()))
            else:
                # 分析必须只读：解包封包用临时目录，分析完立即清理
                work = Path(tempfile.mkdtemp(prefix="rtools_analyze_"))
                try:
                    loose = scanner.scan_assets(str(p), probe=True,
                                                progress=scan_log,
                                                cancel=lambda: _job_cancelled(job_id))
                    # 审核修复（中-25）：与优化执行路径对齐传
                    # extract_scripts，避免分析口径与执行口径不一致
                    packed = scanner.scan_rpa_assets(str(p), str(work),
                                                     probe=True, progress=scan_log,
                                                     cancel=lambda: _job_cancelled(job_id),
                                                     extract_scripts=True)
                    report = analyzer.analyze(loose + packed, str(p), "dist")
                finally:
                    shutil.rmtree(work, ignore_errors=True)
                # 防误用：目录里找不到编译脚本，很可能选错了目录
                if not any(f.suffix.lower() == ".rpyc" for f in p.rglob("*.rpyc")):
                    report.warnings.insert(
                        0, "这个目录里没找到 .rpyc 编译脚本，看起来不像已打包的成品。"
                           "请确认选的是解压后含 exe 和 game 文件夹的那个目录；"
                           "如果你选的其实是工程目录，请切到左边的「超级打包器」标签。")
            _job_log(job_id, "done", "分析完成")
            result = {"mode": mode, "report": report.to_dict()}
            if mode == "project":
                result["charlist"] = charlist_str
            return result
        finally:
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)

    _run_in_thread(job_id, task)
    return {"ok": True, "job": job_id}


@app.post("/api/optimize")
def api_optimize(req: OptimizeReq):
    path = Path(req.path)
    if not path.exists():
        return JSONResponse({"ok": False, "error": f"路径不存在：{req.path}"})
    if archives.is_archive(str(path)):
        mode = req.mode if req.mode in ("project", "dist") else "dist"
    else:
        mode = req.mode or _detect_mode(path)
    opts = _options_from_dict(req.options)
    work_root = req.work_root or str(path.parent / "_rtools_work")
    output_dir = req.output or str(path.parent / "_rtools_output")
    Path(work_root).mkdir(parents=True, exist_ok=True)

    job_id = _new_job("optimize")
    progress = Progress(lambda s, m: _job_log(job_id, s, m))

    def task():
        if mode == "project":
            r = pipeline.run_project(str(path), opts, work_root, output_dir,
                                     progress,
                                     cancel=lambda: _job_cancelled(job_id))
        else:
            # run_dist_smart 兼容目录与压缩包输入，压缩包会自动回包
            r = pipeline.run_dist_smart(str(path), opts, work_root,
                                        output_dir, progress,
                                        password=req.password,
                                        cancel=lambda: _job_cancelled(job_id))
        return _clean_result(r)

    _dispatch_job(job_id, task)
    return {"ok": True, "job": job_id}


@app.post("/api/package")
def api_package(req: PackageReq):
    sdk = packager.find_sdk(req.sdk)
    if not sdk:
        return JSONResponse({"ok": False,
                             "error": "找不到 Ren'Py SDK，请在设置里指定 SDK 目录"})
    job_id = _new_job("package")

    def task():
        return packager.package_project(sdk, req.path, req.platforms,
                                        req.destination,
                                        log=lambda m: _job_log(job_id, "package", m),
                                        archive_rpa=req.archive_rpa)

    _dispatch_job(job_id, task)
    return {"ok": True, "job": job_id, "sdk": sdk}


@app.post("/api/full")
def api_full(req: FullReq):
    path = Path(req.path)
    if not (path / "game").is_dir():
        return JSONResponse({"ok": False,
                             "error": f"{req.path} 不是有效工程（缺少 game 目录）"})
    sdk = packager.find_sdk(req.sdk)
    if not sdk:
        return JSONResponse({"ok": False,
                             "error": "找不到 Ren'Py SDK，请在设置里指定 SDK 目录"})
    opts = _options_from_dict(req.options)
    work_root = str(path.parent / "_rtools_work")
    output_dir = str(path.parent / "_rtools_output")
    Path(work_root).mkdir(parents=True, exist_ok=True)

    job_id = _new_job("full")
    progress = Progress(lambda s, m: _job_log(job_id, s, m))

    def task():
        opt = pipeline.run_project(str(path), opts, work_root, output_dir,
                                   progress,
                                   cancel=lambda: _job_cancelled(job_id))
        pkg = packager.package_project(sdk, opt["working_dir"], req.platforms,
                                       req.destination,
                                       log=lambda m: _job_log(job_id, "package", m),
                                       archive_rpa=req.archive_rpa)
        return {"optimize": _clean_result(opt), "package": pkg}

    _dispatch_job(job_id, task)
    return {"ok": True, "job": job_id}


@app.get("/api/jobs")
def api_jobs():
    """任务队列总览：排队中/执行中/已结束，供前端队列面板与断线重连用。"""
    with JOBS_LOCK:
        items = [{"id": j["id"], "kind": j["kind"], "status": j["status"],
                  "created": j["created"]}
                 for j in JOBS.values()]
    items.sort(key=lambda x: x["created"])
    return {"ok": True, "jobs": items}


@app.get("/api/job/{job_id}")
def api_job(job_id: str, since: int = 0):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return JSONResponse({"ok": False, "error": "任务不存在"})
        logs = job["logs"][since:]
        return {"ok": True, "status": job["status"],
                "kind": job["kind"],
                "logs": logs, "next": since + len(logs),
                "result": job["result"], "error": job["error"],
                "cancel_requested": job["cancel"]}


@app.post("/api/job/{job_id}/cancel")
def api_job_cancel(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return JSONResponse({"ok": False, "error": "任务不存在"})
        if job["status"] == "queued":
            # 还在排队：直接退队，调度器见到非 queued 状态会自动跳过
            job["status"] = "canceled"
            job["error"] = "用户已取消（任务尚未开始）。"
        else:
            job["cancel"] = True
    return {"ok": True}


@app.get("/api/update")
def api_update():
    from rtools import updater
    info = updater.check_update()
    return {"ok": True, "info": info}


@app.get("/api/sdk")
def api_get_sdk():
    return {"ok": True, "sdk_path": packager.load_config().get("sdk_path")}


# 审核修复（中-28）：tkinter 非线程安全，选择框必须串行化，
# 连点两下浏览按钮曾有两个并发 Tk 实例带崩服务进程的风险
_BROWSE_LOCK = threading.Lock()


@app.get("/api/browse")
def api_browse(kind: str = "file"):
    """在服务端弹出原生文件/文件夹选择框（本机工具的特权，网页做不到）。"""
    if not _BROWSE_LOCK.acquire(blocking=False):
        return JSONResponse({"ok": False,
                             "error": "已有一个选择框打开，请先在那边完成选择。"})
    result: dict = {}

    def run():
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            if kind == "dir":
                p = filedialog.askdirectory(title="选择文件夹")
            elif kind == "font":
                p = filedialog.askopenfilename(
                    title="选择字体文件",
                    filetypes=[("字体", "*.ttf *.otf *.ttc *.otc"),
                               ("所有文件", "*.*")])
            elif kind == "apk":
                p = filedialog.askopenfilename(
                    title="选择 APK 文件",
                    filetypes=[("安卓安装包", "*.apk"),
                               ("所有文件", "*.*")])
            else:
                p = filedialog.askopenfilename(
                    title="选择压缩包或成品文件",
                    filetypes=[("压缩包", "*.zip *.7z *.rar"),
                               ("所有文件", "*.*")])
            root.destroy()
            result["path"] = p or ""
        except Exception as e:
            result["error"] = str(e)

    t = threading.Thread(target=run)
    t.start()
    t.join(180)
    try:
        if t.is_alive():
            return JSONResponse({"ok": False, "error": "选择框超时，请重试。"})
        if "error" in result:
            return JSONResponse({"ok": False,
                                 "error": "无法弹出选择框：" + result["error"]})
        return {"ok": True, "path": result.get("path", "")}
    finally:
        _BROWSE_LOCK.release()


@app.get("/api/recent")
def api_recent():
    cfg = packager.load_config()
    return {"ok": True, "recent": cfg.get("recent_paths", [])}


@app.post("/api/recent")
def api_add_recent(body: dict):
    p = (body.get("path") or "").strip()
    if not p:
        return JSONResponse({"ok": False, "error": "路径为空"})
    cfg = packager.load_config()
    rec = [x for x in cfg.get("recent_paths", []) if x != p]
    rec.insert(0, p)
    cfg["recent_paths"] = rec[:6]
    packager.save_config(cfg)
    return {"ok": True, "recent": cfg["recent_paths"]}


@app.post("/api/sdk")
def api_set_sdk(body: dict):
    path = body.get("sdk_path", "")
    if not path or not (Path(path) / "renpy.exe").exists():
        return JSONResponse({"ok": False,
                             "error": "该目录下找不到 renpy.exe，请检查路径"})
    cfg = packager.load_config()
    cfg["sdk_path"] = path
    packager.save_config(cfg)
    return {"ok": True}


@app.post("/api/slimfont")
def api_slimfont(req: FontSlimReq):
    """独立字体瘦身：选字体 + 文本来源，输出瘦身字体与字符清单。"""
    if not Path(req.font).exists():
        return JSONResponse({"ok": False, "error": f"字体文件不存在：{req.font}"})
    if not req.sources:
        return JSONResponse({"ok": False, "error": "请至少提供一个文本来源"})

    csd = req.charset or {}
    cs = CharsetOptions()
    cs.base_latin = csd.get("base_latin", True)
    cs.cjk_punct = csd.get("cjk_punct", True)
    cs.fullwidth = csd.get("fullwidth", False)
    cs.kana = csd.get("kana", False)
    cs.extra_chars = csd.get("extra_chars", "")

    job_id = _new_job("slimfont")
    progress = Progress(lambda s, m: _job_log(job_id, s, m))

    def task():
        return font_tool.run_font_slim(req.font, req.sources, cs,
                                       progress=progress)

    _dispatch_job(job_id, task)
    return {"ok": True, "job": job_id}


@app.post("/api/slimapk")
def api_slimapk(req: SlimApkReq):
    """APK 瘦身（图形界面入口，与 CLI slimapk 同一引擎）。"""
    if not Path(req.path).exists():
        return JSONResponse({"ok": False, "error": f"APK 不存在：{req.path}"})
    job_id = _new_job("slimapk")
    progress = Progress(lambda s, m: _job_log(job_id, s, m))

    def task():
        r = apk.slim_apk(req.path, req.preset,
                          sdk=packager.find_sdk(),
                          keystore=req.keystore, ks_pass=req.ks_pass,
                          key_alias=req.key_alias,
                          generate_key=req.gen_key,
                          remap_convert=req.remap,
                          progress=progress)
        r = dict(r)
        if r.get("keystore"):
            r["keystore"] = dict(r["keystore"])   # 保证可 JSON 序列化
        return r

    _dispatch_job(job_id, task)
    return {"ok": True, "job": job_id}


@app.post("/api/shutdown")
def api_shutdown():
    """退出后台服务（界面"退出工具"按钮与托盘菜单共用）。

    先回应请求，稍后干净退出，保证前端能收到结果。
    """
    from rtools import runtime

    def _die():
        time.sleep(0.6)
        runtime.terminate()

    threading.Thread(target=_die, daemon=True).start()
    return {"ok": True}


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
