"""Web 与 CLI 的分析口径必须一致（回归：两份实现曾经漂过）。

历史上 `cli.cmd_analyze` 与 `web.app.api_analyze` 各写了一份分析逻辑，
结果是同一份输入在两个入口得到不同结论：

- 压缩包里有多个成品目录时，CLI 直接拒绝并要求拆包，
  Web 用 `find_dist_root` 只取第一个，其余平台**静默丢弃**；
- 带 `.rpy` 源码的压缩包，Web 判成 project（报"缺少 game 目录"的
  误导错），CLI 判成 dist。

现在两者都走 `rtools.analysis.analyze_input`。这组用例锁的就是
"只剩一份实现"这件事本身，而不是某个具体行为——行为回归由
各自入口的用例覆盖。
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from rtools import analysis


def _zip_with(tmp_path: Path, name: str, files: dict[str, bytes]) -> Path:
    import zipfile
    target = tmp_path / name
    with zipfile.ZipFile(target, "w") as zf:
        for arc, payload in files.items():
            zf.writestr(arc, payload)
    return target


def test_both_entries_call_the_shared_analysis():
    """CLI 与 Web 的分析入口都必须调用 analysis.analyze_input。

    源码级断言看着粗暴，但它拦的正是"有人图省事又抄一份"这种回归——
    抄出来的第二份不会有任何行为差异提示，只能靠这里发现。
    """
    import cli
    import web.app as web_app

    assert "analyze_input" in inspect.getsource(cli.cmd_analyze)
    assert "analyze_input" in inspect.getsource(web_app.api_analyze)


def test_mode_detection_is_shared():
    """工程/成品判定只留一份：Web 的 _detect_mode 就是 analysis 的那个。"""
    import web.app as web_app

    assert web_app._detect_mode is analysis.detect_mode


def test_archive_with_multiple_products_is_rejected(tmp_path):
    """多成品压缩包一律拒绝，不静默只取第一个（旧 Web 的问题）。

    两个顶层目录各带 game/*.rpyc，find_dist_roots 会认为有两个成品。
    """
    payload = {"Game-PC/game/script.rpyc": b"RENPY RPC2\x00",
               "Game-Mac/game/script.rpyc": b"RENPY RPC2\x00"}
    archive = _zip_with(tmp_path, "multi.zip", payload)
    with pytest.raises(analysis.AnalyzeError) as exc:
        analysis.analyze_input(str(archive))
    assert "2 个成品目录" in str(exc.value)
    assert "拆开" in str(exc.value)


def test_archive_never_analyzed_as_project(tmp_path):
    """压缩包按成品处理，即使包里带 .rpy 源码。

    与 AGENTS「压缩包一律按成品处理」、optimize 的口径一致。
    """
    payload = {"Game/game/script.rpy": b'label start:\n    "hi"\n'}
    archive = _zip_with(tmp_path, "src.zip", payload)
    result = analysis.analyze_input(str(archive))
    assert result["mode"] == "dist"


def test_explicit_project_mode_on_archive_is_corrected(tmp_path):
    """显式 --mode project 对压缩包只警告并纠正成 dist，不报错。"""
    payload = {"Game/game/script.rpyc": b"RENPY RPC2\x00"}
    archive = _zip_with(tmp_path, "one.zip", payload)
    seen = []

    def progress(stage, message, code="", params=None):
        seen.append((stage, code))

    result = analysis.analyze_input(str(archive), mode="project",
                                    progress=progress)
    assert result["mode"] == "dist"
    assert ("warn", "mode_corrected") in seen


def test_missing_path_raises_analyze_error(tmp_path):
    with pytest.raises(analysis.AnalyzeError):
        analysis.analyze_input(str(tmp_path / "nope"))
