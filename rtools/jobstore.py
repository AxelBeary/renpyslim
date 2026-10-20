"""任务历史持久化：任务结束后仍能查到做过什么，重启不丢。

存储位置：环境变量 RENPYSLIM_HOME（若有）否则 ~/.renpyslim 下的 jobs.json。
运行态靠心跳时间 + 进程判活识别遗留任务，避免把同机其它进程的活动任务误判。
只记摘要不记日志全文；文件损坏或磁盘写失败都不影响主流程。
"""
from __future__ import annotations

import ctypes
import json
import logging
import math
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_JOBS = 50
DEFAULT_LIMIT = 20
STALE_SECONDS = 180
HEARTBEAT_MIN_INTERVAL = 5.0
_VERSION = 1

_WIN_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WIN_ERROR_ACCESS_DENIED = 5
_WIN_STILL_ACTIVE = 259

_LOCK = threading.RLock()
_DROP = object()


def _store_path() -> Path:
    env = os.environ.get("RENPYSLIM_HOME")
    home = Path(env) if env else Path.home() / ".renpyslim"
    return home / "jobs.json"


def _jsonable(value: Any, depth: int = 0) -> Any:
    """把任意对象清洗成可 JSON 序列化的形状；实在转不了就丢弃。"""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Path):
        return str(value)
    if depth >= 6:
        try:
            return str(value)
        except Exception:
            return _DROP
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            converted = _jsonable(item, depth + 1)
            if converted is not _DROP:
                cleaned[str(key)] = converted
        return cleaned
    if isinstance(value, (list, tuple, set, frozenset)):
        converted_items = []
        for item in value:
            converted = _jsonable(item, depth + 1)
            if converted is not _DROP:
                converted_items.append(converted)
        return converted_items
    try:
        return str(value)
    except Exception:
        return _DROP


def _clean_summary(summary: Any) -> dict:
    if summary is None:
        return {}
    if not isinstance(summary, dict):
        summary = {"value": summary}
    cleaned = _jsonable(summary)
    if not isinstance(cleaned, dict):
        return {"value": str(summary)}
    try:
        json.dumps(cleaned, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return {"value": repr(summary)}
    return cleaned


def _as_number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return default


def _as_optional_number(value: Any) -> float | None:
    if value is None:
        return None
    number = _as_number(value, default=-1.0)
    return number if number >= 0 else None


def _normalize(raw: Any) -> dict | None:
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    pid = raw.get("pid")
    created = _as_number(raw.get("created"))
    return {
        "id": str(raw.get("id")),
        "kind": str(raw.get("kind") or "unknown"),
        "status": str(raw.get("status") or "unknown"),
        "created": created,
        "heartbeat": _as_number(raw.get("heartbeat"), default=created),
        "finished": _as_optional_number(raw.get("finished")),
        "title": str(raw.get("title") or ""),
        "pid": pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
        "summary": _clean_summary(raw.get("summary")),
        "error": str(raw["error"]) if raw.get("error") else None,
    }


def _pid_alive(pid: Any) -> bool:
    """进程判活。查询被拒绝（权限不足）时按活着处理，避免误判中断。"""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (
            ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = (
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(
            _WIN_PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
        if not handle:
            return ctypes.get_last_error() == _WIN_ERROR_ACCESS_DENIED
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == _WIN_STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _load_unlocked() -> list[dict]:
    path = _store_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        logger.warning("任务历史文件损坏或不可读（%s），按空历史处理：%s",
                       path, exc)
        return []
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        logger.warning("任务历史文件结构异常（%s），按空历史处理", path)
        return []
    jobs = [job for job in (_normalize(item) for item in payload["jobs"]) if job]
    jobs.sort(key=lambda job: job["created"], reverse=True)
    return jobs


def _save_unlocked(jobs: list[dict]) -> None:
    path = _store_path()
    tmp: Path | None = None
    try:
        jobs = sorted(jobs, key=lambda job: job["created"], reverse=True)[:MAX_JOBS]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"version": _VERSION, "jobs": jobs}, handle,
                      ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("写入任务历史失败（%s）：%s", path, exc)
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def record_start(job_id: str, kind: str, title: str = "",
                 pid: int | None = None) -> dict:
    """登记一个开始运行的任务；返回写入的记录。"""
    if not isinstance(pid, int) or isinstance(pid, bool):
        pid = os.getpid()
    now = time.time()
    record = {
        "id": str(job_id),
        "kind": str(kind or "unknown"),
        "status": "running",
        "created": now,
        "heartbeat": now,
        "finished": None,
        "title": str(title or ""),
        "pid": pid,
        "summary": {},
        "error": None,
    }
    with _LOCK:
        jobs = [job for job in _load_unlocked() if job["id"] != record["id"]]
        jobs.insert(0, record)
        _save_unlocked(jobs)
    return dict(record)


def set_title(job_id: str, title: str) -> bool:
    """给任务补标题（如游戏名）；找不到任务返回 False。"""
    with _LOCK:
        jobs = _load_unlocked()
        for job in jobs:
            if job["id"] == str(job_id):
                job["title"] = str(title or "")
                _save_unlocked(jobs)
                return True
    return False


def heartbeat(job_id: str, force: bool = False) -> bool:
    """刷新任务心跳；默认 5 秒内的重复调用不写盘，force=True 强制写。"""
    now = time.time()
    with _LOCK:
        jobs = _load_unlocked()
        for job in jobs:
            if job["id"] == str(job_id):
                if (not force
                        and now - job["heartbeat"] < HEARTBEAT_MIN_INTERVAL):
                    return True
                job["heartbeat"] = now
                _save_unlocked(jobs)
                return True
    return False


def record_finish(job_id: str, status: str, summary: Any = None,
                  error: Any = None) -> dict:
    """登记任务结束。找不到开始记录时补一条，保证历史不断档。"""
    with _LOCK:
        jobs = _load_unlocked()
        for job in jobs:
            if job["id"] == str(job_id):
                now = time.time()
                job["status"] = str(status)
                job["finished"] = now
                job["heartbeat"] = now
                job["summary"] = _clean_summary(summary)
                job["error"] = str(error) if error else None
                _save_unlocked(jobs)
                return dict(job)
        now = time.time()
        record = {
            "id": str(job_id),
            "kind": "unknown",
            "status": str(status),
            "created": now,
            "heartbeat": now,
            "finished": now,
            "title": "",
            "pid": os.getpid(),
            "summary": _clean_summary(summary),
            "error": str(error) if error else None,
        }
        jobs.insert(0, record)
        _save_unlocked(jobs)
        return dict(record)


def list_jobs(limit: int = DEFAULT_LIMIT) -> list[dict]:
    """按开始时间倒序返回任务；遗留运行态按心跳与进程判活改判中断。"""
    try:
        limit = max(0, min(int(limit), MAX_JOBS))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    with _LOCK:
        jobs = _load_unlocked()
        changed = False
        now = time.time()
        for job in jobs:
            if job["status"] not in ("running", "queued"):
                continue
            stale = now - job["heartbeat"] > STALE_SECONDS
            if stale or not _pid_alive(job.get("pid")):
                job["status"] = "interrupted"
                job["error"] = "工具已退出，任务中断"
                if job.get("finished") is None:
                    job["finished"] = now
                changed = True
        if changed:
            _save_unlocked(jobs)
        return [dict(job) for job in jobs[:limit]]


def clear() -> int:
    """清空任务历史，返回清掉的条数。"""
    with _LOCK:
        jobs = _load_unlocked()
        _save_unlocked([])
        return len(jobs)
