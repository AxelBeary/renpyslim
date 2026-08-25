"""Web 服务：FastAPI 应用，图形界面入口的后端。

任务在后台线程执行，前端通过轮询 /api/job/{id} 获取进度与日志。
"""
from __future__ import annotations

import logging
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
_LOG = logging.getLogger("renpyslim.web")

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
    # Origin 头只要存在就必须能解析出本机主机名：空串、"null"、
    # 非法值一律拒绝（旧版解析不出主机就放行，可被绕过）
    if "origin" in request.headers:
        from urllib.parse import urlparse
        origin = request.headers.get("origin", "")
        origin_host = (urlparse(origin).hostname or "").lower()
        if origin_host not in _ALLOWED_HOSTS:
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
        # 线程收尾 KeyError、状态永久卡死、还写假崩溃转储；
        # 排队中的任务同样豁免，否则队首还没开跑就被清掉、永远无人唤醒
        now = time.time()
        for k in [k for k, v in JOBS.items()
                  if now - v["created"] > 7200
                  and v["status"] not in ("running", "queued")]:
            del JOBS[k]
        if len(JOBS) > 30:
            for k, _ in sorted(JOBS.items(), key=lambda kv: kv[1]["created"])[:-30]:
                if JOBS[k]["status"] not in ("running", "queued"):
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
    busy 判断与 _RUNNING 占位在同一把 _QUEUE_LOCK 内原子完成，
    杜绝“两个提交都看到空闲、双双开跑”的竞态窗口。
    """
    with _QUEUE_LOCK:
        busy = _RUNNING["id"] is not None
        if not busy:
            _RUNNING["id"] = job_id
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job:
                    job["status"] = "running"
            _run_in_thread(job_id, fn)
            return
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job["status"] = "queued"
        _JOB_TASKS[job_id] = fn
        _QUEUE.append(job_id)


def _scheduler_loop():
    """后台调度：每轮只启动队首一个排队任务后立即结束本轮。

    下一个任务由当前任务收尾时 _notify_job_done 链式唤醒启动，
    保证任意时刻最多一个重任务在跑（旧版一轮循环弹出全部任务并
    全部点火，串行保证失效）。
    """
    with _QUEUE_LOCK:
        if _RUNNING["id"] is not None:
            return   # 已有任务在跑（可能是唤醒间隙新提交的），让位
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
        final = {"status": "done", "result": None, "error": None,
                 "traceback": None}
        try:
            final["result"] = fn()
        except pipeline.PipelineCancelled:
            final["status"] = "canceled"
            final["error"] = "用户已取消，已完成的部分成果保留在结果目录。"
        except scanner.ScanCancelled:
            final["status"] = "canceled"
            final["error"] = "用户已取消扫描。"
        except Exception as e:
            from rtools import crashdump
            dump = crashdump.write_crash(kind)
            final["status"] = "error"
            final["error"] = str(e) + (
                f"\n崩溃详情已存：{dump}" if dump else "")
            final["traceback"] = traceback.format_exc(limit=5)
        finally:
            # 审核修复：状态落终与 _RUNNING 清位在同一把 _QUEUE_LOCK 内
            # 原子完成，再链式唤醒调度——关闭旧版“状态已 done 但占位未清”
            # 窗口，杜绝两个重任务同时在跑
            with _QUEUE_LOCK:
                with JOBS_LOCK:
                    job = JOBS.get(job_id)
                    if job:
                        job["status"] = final["status"]
                        if final["result"] is not None:
                            job["result"] = final["result"]
                        if final["error"] is not None:
                            job["error"] = final["error"]
                        if final["traceback"]:
                            job["logs"].append(
                                {"t": time.time(), "stage": "error",
                                 "message": final["traceback"]})
                if _RUNNING["id"] == job_id:
                    _RUNNING["id"] = None
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
    mode: Optional[str] = None
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
    key_pass: Optional[str] = None
    new_key_password: Optional[str] = None


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
                report.languages = charset.detect_languages(game)
                # 字体使用处数：前端分析报告与 CLI 同口径；
                # languages 字段在分析报告里如实展示检测到的语言清单。
                from rtools.refs import RefIndex
                from rtools import cleanup as _cleanup
                from rtools.models import AssetKind
                ref_index = RefIndex(game)
                fonts = [a for a in assets if a.kind == AssetKind.FONT
                         and a.ext in (".ttf", ".otf")]
                report.font_usage, usage_warns = _cleanup.font_usage_report(
                    ref_index, fonts)
                report.warnings.extend(usage_warns)
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
                    report.languages = charset.detect_languages(str(p))
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
    # mode 口径：只认 project/dist/不传（自动检测），其余非法值 400；
    # 压缩包输入一律走 dist——压缩包是成品形态，不存在工程模式，
    # 用户显式传 project 只记日志警告并纠正，不报错打断。
    if req.mode not in ("project", "dist", None):
        return JSONResponse({"ok": False,
                             "error": f"非法的 mode 值：{req.mode}，"
                                      "只支持 project / dist / 不传（自动判断）"},
                            status_code=400)
    path = Path(req.path)
    if not path.exists():
        return JSONResponse({"ok": False, "error": f"路径不存在：{req.path}"})
    if archives.is_archive(str(path)):
        if req.mode == "project":
            _LOG.warning("optimize：压缩包输入不支持 project 模式，"
                         "已自动改走 dist")
        mode = "dist"
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
# 连点两下浏览按钮曾有两个并发 Tk 实例带崩服务进程的风险。
# 收口修复（2026-08-23 审查 C 路）：旧方案锁由对话框线程自己释放、
# 超时不放锁——对话框卡死/被遮挡/远程桌面挂起时线程永不结束，
# 锁在进程余生内不再释放，浏览功能彻底死掉。兜底：锁状态显式记录
# 获锁时间戳与代际令牌，下一次请求发现持有超过上限即视为卡死，
# 打警告后强制重置、允许新对话框；旧线程迟到苏醒时 finally 释放
# 按代际核对，不是自己那一代就不动新锁（幂等保护）。
_BROWSE_META_LOCK = threading.Lock()
_BROWSE_STATE = {"held": False, "since": None, "token": 0}
_BROWSE_LOCK_MAX_HOLD = 600.0    # 秒：锁持有超过此时长即强制重置（测试可改）
_BROWSE_JOIN_TIMEOUT = 180.0     # 秒：等对话框线程返回的时长（测试可注入）


def _browse_try_acquire() -> Optional[int]:
    """尝试占用浏览锁：成功返回本次的代际令牌，被占用返回 None。

    锁持有超过 _BROWSE_LOCK_MAX_HOLD 视为对话框卡死：打警告后
    强制重置状态，让新请求得以开新对话框。
    """
    with _BROWSE_META_LOCK:
        now = time.time()
        if _BROWSE_STATE["held"]:
            held = now - (_BROWSE_STATE["since"] or now)
            if held <= _BROWSE_LOCK_MAX_HOLD:
                return None
            _LOG.warning("browse：选择框锁已持有 %.0f 秒超过 %.0f 秒上限，"
                         "疑似对话框卡死，强制重置锁状态。",
                         held, _BROWSE_LOCK_MAX_HOLD)
        _BROWSE_STATE["held"] = True
        _BROWSE_STATE["since"] = now
        _BROWSE_STATE["token"] += 1
        return _BROWSE_STATE["token"]


def _browse_release(token: int) -> None:
    """幂等放锁：只对得上代际令牌的持有者生效。

    被强制重置过的旧线程迟到苏醒时，其令牌已过期，释放直接忽略，
    避免误放新一代对话框的锁。
    """
    with _BROWSE_META_LOCK:
        if token != _BROWSE_STATE["token"]:
            return
        _BROWSE_STATE["held"] = False
        _BROWSE_STATE["since"] = None


def _browse_open_dialog(kind: str) -> str:
    """弹原生选择框并返回选中路径（取消为空串）。独立成函数便于回归测试替换。"""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
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
    finally:
        root.destroy()
    return p or ""


@app.get("/api/browse")
def api_browse(kind: str = "file"):
    """在服务端弹出原生文件/文件夹选择框（本机工具的特权，网页做不到）。"""
    token = _browse_try_acquire()
    if token is None:
        return JSONResponse({"ok": False,
                             "error": "已有一个选择框打开，请先在那边完成选择。"})
    result: dict = {}

    def run():
        try:
            result["path"] = _browse_open_dialog(kind)
        except Exception as e:
            result["error"] = str(e)
        finally:
            # 锁必须由线程自己在真正结束时释放（旧版 join 超时后照样放锁，
            # 旧线程手里的 Tk 还活着，会开出第二个并发 Tk 实例带崩进程）；
            # 超时后锁保持占用是期望行为——但持有超过上限会被强制重置，
            # 此处按代际令牌幂等释放，不会误伤重置后的新锁。
            _browse_release(token)

    t = threading.Thread(target=run)
    t.start()
    t.join(_BROWSE_JOIN_TIMEOUT)
    if t.is_alive():
        return JSONResponse({"ok": False,
                             "error": "选择框超时，请重试。"
                                      "若选择框仍开着，请先在那边完成选择；"
                                      "若找不到对话框，请重启工具。"})
    if "error" in result:
        return JSONResponse({"ok": False,
                             "error": "无法弹出选择框：" + result["error"]})
    return {"ok": True, "path": result.get("path", "")}


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
                          key_pass=req.key_pass,
                          generate_key=req.gen_key,
                          new_key_password=req.new_key_password,
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
