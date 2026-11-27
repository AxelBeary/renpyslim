"""issue #42：AV1 的引擎版本门控必须两条线同口径、且 fail-closed。

口径（维护者拍板）：
1. 有 game/script_version.txt → 按它判定（>= 8.0 才放行）；
2. 没有 → 用本机 SDK 版本兜底（工程模式的真实目标引擎就是本机 SDK）；
3. 两者都拿不到 → 停用 AV1，并给出说明"为何停用 / 怎么解除"的中文提示。

判定只有一份实现：rtools/packager.py::av1_decision（内部再调 _av1_engine_version
取值）；工程模式（run_project）与成品模式（run_dist）都只调 packager.av1_decision，
不允许各写一份 if。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rtools import packager, pipeline
from rtools.config import OptimizeOptions
from rtools.image_optimizer import OptimizeResult
from rtools.models import AssetInfo, AssetKind


# ---------------------------------------------------------------------------
# 纯判定层：_av1_engine_version 取版本、av1_decision 下定论、parse_sdk_version
# ---------------------------------------------------------------------------

def _enabled(options, game_dir) -> bool:
    """av1_decision 的布尔视角（测试里读起来更直接）。"""
    return packager.av1_decision(options, game_dir)[0]


def _no_sdk(monkeypatch):
    """模拟"本机没有 SDK"：find_sdk 与 sdk_version 都拿不到版本。"""
    monkeypatch.setattr(packager, "find_sdk", lambda *a, **kw: None)


def _sdk_reporting(monkeypatch, version_text):
    monkeypatch.setattr(packager, "find_sdk",
                        lambda *a, **kw: "C:/fake-sdk")
    monkeypatch.setattr(packager, "sdk_version",
                        lambda *a, **kw: version_text)


def test_script_version_ge_8_allows_av1(tmp_path, monkeypatch):
    _no_sdk(monkeypatch)                       # 有版本文件就不该去问 SDK
    game = tmp_path / "game"
    game.mkdir()
    (game / "script_version.txt").write_text("(8, 3, 4)", encoding="utf-8")
    version, source = packager._av1_engine_version(game)
    assert version == (8, 3, 4) and source == "script_version.txt"
    assert _enabled(OptimizeOptions(experimental_av1=True), game)


def test_script_version_below_8_disables_av1(tmp_path, monkeypatch):
    """版本文件说 7.4，哪怕本机 SDK 是 8.x 也必须停用（文件更权威）。"""
    _sdk_reporting(monkeypatch, "Ren'Py 8.3.4.2201")
    game = tmp_path / "game"
    game.mkdir()
    (game / "script_version.txt").write_text("(7, 4, 0)", encoding="utf-8")
    version, source = packager._av1_engine_version(game)
    assert version == (7, 4, 0) and source == "script_version.txt"
    assert not _enabled(OptimizeOptions(experimental_av1=True), game)


def test_unparsable_script_version_does_not_fall_back_to_sdk(tmp_path,
                                                             monkeypatch):
    """版本文件在但读不出：当作拿不到，绝不拿本机 SDK 去猜（会掩盖问题）。"""
    _sdk_reporting(monkeypatch, "Ren'Py 8.3.4.2201")
    game = tmp_path / "game"
    game.mkdir()
    (game / "script_version.txt").write_text("这不是版本号", encoding="utf-8")
    assert packager._av1_engine_version(game) == (None, None)
    assert not _enabled(OptimizeOptions(experimental_av1=True), game)


def test_missing_script_version_uses_sdk_version(tmp_path, monkeypatch):
    """工程模式主场景：没有版本文件，本机 SDK 8.3 → 放行且来源是本机 SDK。"""
    _sdk_reporting(monkeypatch, "Ren'Py 8.3.4.2201")
    game = tmp_path / "game"
    game.mkdir()
    version, source = packager._av1_engine_version(game)
    assert version == (8, 3, 4) and source == "本机 SDK"
    assert _enabled(OptimizeOptions(experimental_av1=True), game)


def test_missing_script_version_with_old_sdk_disables_av1(tmp_path,
                                                          monkeypatch):
    _sdk_reporting(monkeypatch, "Ren'Py 7.6.3.23091805")
    game = tmp_path / "game"
    game.mkdir()
    assert packager._av1_engine_version(game)[0] == (7, 6, 3)
    assert not _enabled(OptimizeOptions(experimental_av1=True), game)


def test_no_script_version_and_no_sdk_disables_av1(tmp_path, monkeypatch):
    """两者都没有 → fail-closed。"""
    _no_sdk(monkeypatch)
    game = tmp_path / "game"
    game.mkdir()
    assert packager._av1_engine_version(game) == (None, None)
    assert not _enabled(OptimizeOptions(experimental_av1=True), game)


def test_explicit_sdk_overrides_guess_and_is_used(tmp_path, monkeypatch):
    """显式 SDK 优先：工程模式首跑时它还没被 find_sdk 记住，必须直接生效。

    full / optimize --mode project 在打包前就判定 AV1，若门控只按常规位置
    探测，用户用 --sdk 指定的 8.3 会被误判成"拿不到 SDK"，提示里给出的
    解除办法也就失效了。
    """
    probed = []

    def fake_find_sdk(explicit=None):
        probed.append(explicit)
        return explicit                    # 只有显式传入的才算找得到

    monkeypatch.setattr(packager, "find_sdk", fake_find_sdk)
    monkeypatch.setattr(packager, "sdk_version",
                        lambda *a, **kw: "Ren'Py 8.3.4.2201")
    game = tmp_path / "game"
    game.mkdir()
    opts = OptimizeOptions(experimental_av1=True)
    assert packager.av1_decision(opts, game, "D:/renpy-8.3") == (True, "本机 SDK")
    assert probed == ["D:/renpy-8.3"]
    # 不传显式 SDK 时仍按常规位置探测（探测不到 → 停用）
    assert packager.av1_decision(opts, game) == (False, None)
    assert probed[-1] is None


def test_av1_off_never_asks_sdk(tmp_path, monkeypatch):
    """开关关着时不该去起 SDK 子进程（renpy --version 有开销）。"""
    def _boom(*a, **kw):
        pytest.fail("用户没开 AV1，不应调用 SDK 去判定版本")
    monkeypatch.setattr(packager, "find_sdk", _boom)
    game = tmp_path / "game"
    game.mkdir()
    assert _enabled(OptimizeOptions(), game) is False


@pytest.mark.parametrize("text, expected", [
    ("Ren'Py 8.3.4.2201", (8, 3, 4)),
    ("Ren'Py 8.3", (8, 3, 0)),
    ("Ren'Py 7.4.11.2266", (7, 4, 11)),
    ("8.0.0", (8, 0, 0)),
    ("未知版本", None),
    ("", None),
    ("no digits here", None),
])
def test_parse_sdk_version(text, expected):
    assert packager.parse_sdk_version(text) == expected


def test_av1_disabled_warning_names_both_escapes():
    """停用提示必须说清"为什么"和"怎么解除"（写版本文件 / 指定 SDK）。"""
    text = packager.AV1_DISABLED_WARNING
    assert "已跳过" in text
    assert "Ren'Py 8.0 及以上" in text, "没说清 AV1 要求的最低引擎版本"
    # 不能把要求的最低版本写成看起来像"检测到的版本"的样子
    assert "无法确认目标引擎版本" in text
    assert "script_version.txt" in text
    assert "--sdk" in text


# ---------------------------------------------------------------------------
# 两条流水线接的是同一个门控
# ---------------------------------------------------------------------------

def _fake_scan_video(game_dir, **kwargs):
    """假扫描：总是报一个 movie.webm（dist 与 project 的扫描根不同）。

    大小取一个很小的固定值——视频重编码在图/音/字全关的用例里跑，
    不依赖真实体积。
    """
    path = Path(game_dir) / "movie.webm"
    return [AssetInfo(str(path), "movie.webm", AssetKind.VIDEO, 12)]


def _patch_video(monkeypatch, calls):
    def fake_compress(src, dst, preset="conservative", use_av1=False):
        calls.append(use_av1)
        Path(dst).write_bytes(b"AV1" if use_av1 else b"VP9")
        return OptimizeResult(status="ok", path=dst,
                              old_size=Path(src).stat().st_size, new_size=3)
    monkeypatch.setattr(pipeline, "find_ffmpeg", lambda: "fake-ffmpeg")
    monkeypatch.setattr(pipeline, "compress_video", fake_compress)
    monkeypatch.setattr(pipeline.scanner, "scan_assets", _fake_scan_video)


def _project_with_video(tmp_path, name, script_version=None):
    project = tmp_path / name
    game = project / "game"
    game.mkdir(parents=True)
    (game / "script.rpy").write_text("label start:\n    pass\n",
                                     encoding="utf-8")
    (game / "movie.webm").write_bytes(b"source-video" * 40)
    if script_version is not None:
        (game / "script_version.txt").write_text(script_version,
                                                 encoding="utf-8")
    return project


def _project_opts():
    return OptimizeOptions(do_images=False, do_audio=False, do_fonts=False,
                           do_videos=True, use_cache=False,
                           experimental_av1=True)


def test_project_mode_no_version_no_sdk_warns_and_uses_normal_codec(
        tmp_path, monkeypatch):
    """工程模式主回归：没有版本文件、没有 SDK → 停用 AV1 且提示清楚。"""
    monkeypatch.setenv("RENPYSLIM_HOME", str(tmp_path / "home"))
    _no_sdk(monkeypatch)
    calls = []
    _patch_video(monkeypatch, calls)
    project = _project_with_video(tmp_path, "Game")

    result = pipeline.run_project(str(project), _project_opts(),
                                  str(tmp_path / "work"),
                                  str(tmp_path / "out"))

    assert calls == [False], "无版本文件且无 SDK 时必须按普通编码处理"
    assert packager.AV1_DISABLED_WARNING in result["warnings"]
    produced = Path(result["working_dir"]) / "game" / "movie.webm"
    assert produced.read_bytes() == b"VP9"


def test_project_mode_sdk_fallback_allows_av1(tmp_path, monkeypatch):
    """工程模式没有版本文件、但本机 SDK >= 8.0 → 放行 AV1。"""
    monkeypatch.setenv("RENPYSLIM_HOME", str(tmp_path / "home"))
    _sdk_reporting(monkeypatch, "Ren'Py 8.3.4.2201")
    calls = []
    _patch_video(monkeypatch, calls)
    project = _project_with_video(tmp_path, "Game")

    result = pipeline.run_project(str(project), _project_opts(),
                                  str(tmp_path / "work"),
                                  str(tmp_path / "out"))

    assert calls == [True]
    assert not any("已跳过" in w for w in result["warnings"])
    assert any("已启用 AV1" in w and "本机 SDK" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# 成品（dist）路径：口径同步，行为不回归
# ---------------------------------------------------------------------------

def _dist_with_video(tmp_path, script_version=None):
    dist = tmp_path / "Game-pc"
    game = dist / "game"
    game.mkdir(parents=True)
    (dist / "Game.exe").write_bytes(b"MZ")
    (game / "movie.webm").write_bytes(b"source-video" * 40)
    if script_version is not None:
        (game / "script_version.txt").write_text(script_version,
                                                 encoding="utf-8")
    return dist


def test_dist_mode_keeps_gating_by_script_version(tmp_path, monkeypatch):
    """dist：版本文件 < 8.0 停用（即使本机 SDK 很新）；提示文案与工程模式同一句。"""
    monkeypatch.setenv("RENPYSLIM_HOME", str(tmp_path / "home"))
    _sdk_reporting(monkeypatch, "Ren'Py 8.3.4.2201")
    calls = []
    _patch_video(monkeypatch, calls)
    dist = _dist_with_video(tmp_path, "(7, 4, 0)")

    result = pipeline.run_dist(str(dist), _project_opts(),
                               str(tmp_path / "work"), str(tmp_path / "out"))

    assert calls == [False]
    assert packager.AV1_DISABLED_WARNING in result["warnings"]


def test_dist_mode_allows_av1_on_8x(tmp_path, monkeypatch):
    monkeypatch.setenv("RENPYSLIM_HOME", str(tmp_path / "home"))
    _no_sdk(monkeypatch)
    calls = []
    _patch_video(monkeypatch, calls)
    dist = _dist_with_video(tmp_path, "(8, 1, 0)")

    result = pipeline.run_dist(str(dist), _project_opts(),
                               str(tmp_path / "work"), str(tmp_path / "out"))

    assert calls == [True]
    assert any("已启用 AV1" in w for w in result["warnings"])


def test_dist_mode_without_version_file_uses_sdk_fallback(tmp_path,
                                                          monkeypatch):
    """dist 无版本文件时口径与工程模式一致：SDK >= 8.0 才放行。"""
    monkeypatch.setenv("RENPYSLIM_HOME", str(tmp_path / "home"))
    _sdk_reporting(monkeypatch, "Ren'Py 8.3.4.2201")
    calls = []
    _patch_video(monkeypatch, calls)
    dist = _dist_with_video(tmp_path)

    result = pipeline.run_dist(str(dist), _project_opts(),
                               str(tmp_path / "work"), str(tmp_path / "out"))

    assert calls == [True]
    assert any("已启用 AV1" in w and "本机 SDK" in w for w in result["warnings"])
