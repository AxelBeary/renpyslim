"""未命名工程打包提示的回归（issue #26 第三阶段）。

官方 distribute 产出的 .sh 按自身文件名找 lib/py3-linux-<arch>/<同名文件>，
工程没设 build.name / config.name 时 executable_name 为空，产物会变成一个
名字为 ".sh" 的怪包、启动直接失败。这里只验证提示的判定口径。
"""
from __future__ import annotations

from pathlib import Path

from rtools import packager


def _proj(tmp_path: Path, options: str | None = None) -> Path:
    proj = tmp_path / "Game"
    (proj / "game").mkdir(parents=True)
    (proj / "game" / "script.rpy").write_text(
        'label start:\n    "hi"\n    return\n', encoding="utf-8")
    if options is not None:
        (proj / "game" / "options.rpy").write_text(options, encoding="utf-8")
    return proj


def test_no_options_file_warns(tmp_path):
    proj = _proj(tmp_path)
    hint = packager.unnamed_project_warning(str(proj))
    assert hint is not None
    assert "build.name" in hint


def test_build_name_suppresses_warning(tmp_path):
    proj = _proj(tmp_path, 'define build.name = "MyGame"\n')
    assert packager.unnamed_project_warning(str(proj)) is None


def test_config_name_suppresses_warning(tmp_path):
    proj = _proj(tmp_path, 'define config.name = "MyGame"\n')
    assert packager.unnamed_project_warning(str(proj)) is None


def test_warning_scans_all_options_files(tmp_path):
    """名字写在别的 .rpy 里也要认：不能见到第一个无 define 的文件就报。"""
    proj = _proj(tmp_path, "define config.version = '1.0'\n")
    (proj / "game" / "zz_names.rpy").write_text(
        'define build.name = "MyGame"\n', encoding="utf-8")
    assert packager.unnamed_project_warning(str(proj)) is None


def test_non_project_dir_is_silent(tmp_path):
    """没有 game/ 的路径不归这里管，交给上层报错。"""
    assert packager.unnamed_project_warning(str(tmp_path)) is None
