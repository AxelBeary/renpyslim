"""Progress 结构化日志兼容层：宽回调带 code/params，旧两参回调不受影响。"""
from __future__ import annotations

from rtools.models import Progress


def test_wide_callback_receives_code_and_params():
    got = []
    progress = Progress(lambda s, m, code=None, params=None:
                        got.append((s, m, code, params)))

    progress.emit("scan", "扫描资源 1/2：a", code="scan_progress",
                  params={"i": 1, "total": 2})

    assert got == [("scan", "扫描资源 1/2：a", "scan_progress",
                    {"i": 1, "total": 2})]


def test_legacy_two_arg_callback_keeps_working():
    got = []
    progress = Progress(lambda stage, message: got.append((stage, message)))

    progress.emit("scan", "扫描资源 1/2：a", code="scan_progress",
                  params={"i": 1, "total": 2})

    assert got == [("scan", "扫描资源 1/2：a")]


def test_missing_callback_is_safe():
    Progress().emit("scan", "msg", code="scan_progress", params={"i": 1})
