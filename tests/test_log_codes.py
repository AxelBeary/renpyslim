"""后端进度日志 code 契约（WP6a）。

静态扫描 pipeline / apk / font_tool 里所有带 code 的 p.emit 调用：
- code 必须是合法 snake_case；
- 核心清单必须全部落地（前端翻译键依赖它，不许悄悄改名/删除）；
- 同一 code 不许跨模块或跨 stage 复用（防语义漂移的重复定义冲突）。
另用 Progress 契约测试确认宽回调能收到 params、旧两参回调行为不变，
并验证并行任务真实发出的 optimize_progress / job_error 携带 params。
"""
from __future__ import annotations

import re
from pathlib import Path

from rtools.models import Progress
from rtools.pipeline import _run_jobs

RT = Path(__file__).resolve().parents[1] / "rtools"
MODULES = ("pipeline.py", "apk.py", "font_tool.py")

# WP6a 核心清单：改名/删除都会让后续前端翻译键失配。
REQUIRED_CODES = {
    # 工程线 / 成品线 / 压缩包分发
    "backup_start", "backup_done", "copy_start", "copy_done",
    "scan_start", "scan_progress", "scan_done", "optimize_progress",
    "job_error", "rewrite_refs", "clean_junk", "quarantine",
    "lint_start", "lint_result", "project_done", "dist_scan",
    "decompile_start", "decompile_done", "rpa_rebuild", "dist_done",
    "unpack_start", "unpack_done", "apk_forward",
    # APK 线
    "apk_progress", "apk_charset", "apk_repack", "apk_align",
    "apk_keygen", "apk_keygen_done", "apk_remap", "apk_done",
    # 字体线
    "font_charset_start", "font_charset_ready", "font_slim_start",
    "font_slim_progress", "font_done", "font_error",
}

_CALL = re.compile(r"p\.emit\(")
_STAGE = re.compile(r'\s*(?:"([a-z_]+)"|([A-Za-z_][\w.]*))')
_CODE = re.compile(r'code="([^"]+)"')
_SNAKE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def _calls(text: str) -> list[tuple[str | None, str | None]]:
    """按 p.emit( 切块，返回每块的 (stage 字面量或 None, code 或 None)。

    切块到下一个 p.emit( 为止，确保不会把“没带 code 的调用”和
    后面调用的 code 配成一对。
    """
    starts = [m.start() for m in _CALL.finditer(text)]
    calls: list[tuple[str | None, str | None]] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(text)
        chunk = text[start:end]
        stage_m = _STAGE.match(chunk[len("p.emit("):])
        code_m = _CODE.search(chunk)
        stage = stage_m.group(1) if stage_m and stage_m.group(1) else None
        calls.append((stage, code_m.group(1) if code_m else None))
    return calls


def _module_calls() -> dict[str, list[tuple[str | None, str | None]]]:
    return {name: _calls((RT / name).read_text(encoding="utf-8"))
            for name in MODULES}


def test_core_codes_present_and_snake_case():
    found = {code
             for calls in _module_calls().values()
             for _stage, code in calls if code}
    missing = REQUIRED_CODES - found
    assert not missing, f"核心进度 code 缺失：{sorted(missing)}"

    bad = sorted(code for code in found if not _SNAKE.fullmatch(code))
    assert not bad, f"code 必须是 snake_case（小写、无空格/中文）：{bad}"


def test_codes_not_defined_twice_with_conflicting_meaning():
    """同一 code 只许出现在一个模块、一个 stage —— 防同名不同义的漂移。"""
    modules: dict[str, set[str]] = {}
    stages: dict[str, set[str]] = {}
    for name, calls in _module_calls().items():
        for stage, code in calls:
            if not code:
                continue
            modules.setdefault(code, set()).add(name)
            if stage:
                stages.setdefault(code, set()).add(stage)

    cross_module = {c: sorted(m) for c, m in modules.items() if len(m) > 1}
    assert not cross_module, f"同一 code 跨模块重复定义：{cross_module}"

    cross_stage = {c: sorted(s) for c, s in stages.items() if len(s) > 1}
    assert not cross_stage, f"同一 code 跨 stage 复用（语义漂移）：{cross_stage}"


def test_wide_callback_receives_code_and_params():
    got = []
    progress = Progress(lambda s, m, code=None, params=None:
                        got.append((s, m, code, params)))

    progress.emit("scan", "扫描资源 1/2：a", code="scan_progress",
                  params={"i": 1, "total": 2, "name": "a"})

    assert got == [("scan", "扫描资源 1/2：a", "scan_progress",
                    {"i": 1, "total": 2, "name": "a"})]


def test_legacy_two_arg_callback_keeps_working():
    got = []
    progress = Progress(lambda stage, message: got.append((stage, message)))

    progress.emit("scan", "扫描资源 1/2：a", code="scan_progress",
                  params={"i": 1, "total": 2, "name": "a"})

    assert got == [("scan", "扫描资源 1/2：a")]


def _ok_result():
    return {"records": [], "saved": 3, "rename": None, "remap": None,
            "rpa": None, "warn": None, "failed": None, "skipped": None}


def test_run_jobs_emits_progress_and_error_with_params():
    """并行任务层真实发出的两条 code 也走宽回调（不跑完整游戏优化）。"""
    got = []
    progress = Progress(lambda s, m, code=None, params=None:
                        got.append((s, m, code, params)))

    def boom():
        raise RuntimeError("坏条目")

    results = _run_jobs(progress, "optimize",
                        [("ok", _ok_result), ("bad", boom)])
    assert len(results) == 2

    codes = {code for _s, _m, code, _p in got if code}
    assert {"optimize_progress", "job_error"} <= codes

    progress_params = next(p for _s, _m, c, p in got
                           if c == "optimize_progress")
    assert progress_params["done"] == 2
    assert progress_params["total"] == 2
    assert set(progress_params) == {"done", "total", "saved"}
    assert isinstance(progress_params["saved"], str)

    error_params = next(p for _s, _m, c, p in got if c == "job_error")
    assert error_params == {"error": "坏条目"}
