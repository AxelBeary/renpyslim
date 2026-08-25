"""启动自检（缺依赖人话指引）的回归测试。

背景：从源码运行且忘装 requirements 时，旧行为是裸 traceback；
现在入口先跑 rtools.missing_dependencies 给安装指引。
"""
from __future__ import annotations

import importlib.util

import rtools


def test_missing_dependencies_all_installed():
    """当前开发环境依赖齐全，自检应为空。"""
    assert rtools.missing_dependencies(gui=False) == []
    assert rtools.missing_dependencies(gui=True) == []


def test_missing_dependencies_reports_absent_pkg(monkeypatch):
    """模拟缺包：能点名缺的是哪个 pip 包。"""
    real = importlib.util.find_spec

    def fake(name, *a, **k):
        if name == "py7zr":
            return None
        return real(name, *a, **k)

    monkeypatch.setattr(importlib.util, "find_spec", fake)
    assert rtools.missing_dependencies(gui=False) == ["py7zr"]


def test_missing_dependencies_gui_includes_web_and_tray(monkeypatch):
    """GUI 口径额外检查 Web 服务与托盘组件依赖。"""
    real = importlib.util.find_spec

    def fake(name, *a, **k):
        if name in ("fastapi", "pystray"):
            return None
        return real(name, *a, **k)

    monkeypatch.setattr(importlib.util, "find_spec", fake)
    # CLI 口径不关心 Web/托盘 → 依然齐全
    assert rtools.missing_dependencies(gui=False) == []
    # GUI 口径点名两个缺口
    assert set(rtools.missing_dependencies(gui=True)) == {"fastapi", "pystray"}


def test_dep_names_cover_requirements_txt():
    """自检名单必须覆盖 requirements.txt 全部包（防新增依赖漏检）。"""
    from pathlib import Path
    req = Path(__file__).resolve().parent.parent / "requirements.txt"
    pkgs = set()
    for line in req.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            pkgs.add(line.split(">=")[0].split("==")[0].lower())
    assert pkgs == set(rtools._DEP_IMPORT_NAMES)
