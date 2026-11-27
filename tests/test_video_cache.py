"""视频缓存必须区分 AV1 开关，避免复用编码格式不同的产物。

契约变更（issue #42）：AV1 的引擎门控口径改成 fail-closed——工程里没有
game/script_version.txt 时按本机 SDK 版本兜底，两者都拿不到就停用 AV1
（详见 rtools/packager.py::av1_decision）。旧用例依赖"无版本文件
时显式开关直接生效"，新契约下那条路径已经不存在，因此本文件相应改写：
无版本文件 + 无 SDK 时，开 AV1 也必须按普通编码处理。
"""
from pathlib import Path

from rtools import cache, packager, pipeline
from rtools.config import OptimizeOptions
from rtools.image_optimizer import OptimizeResult
from rtools.models import AssetInfo, AssetKind


def _patch_video_pipeline(monkeypatch, tmp_path, calls, sdk_version=None):
    """把视频优化链路的真实外部依赖换成桩；sdk_version=None 表示无 SDK。"""

    def fake_compress(src, dst, preset="conservative", use_av1=False):
        calls.append(use_av1)
        Path(dst).write_bytes(b"AV1" if use_av1 else b"VP9")
        old = Path(src).stat().st_size
        return OptimizeResult(status="ok", path=dst, old_size=old, new_size=3)

    def fake_scan(game_dir, **kwargs):
        path = Path(game_dir) / "movie.webm"
        return [AssetInfo(str(path), "movie.webm", AssetKind.VIDEO,
                          path.stat().st_size)]

    monkeypatch.setattr(pipeline, "find_ffmpeg", lambda: "fake-ffmpeg")
    monkeypatch.setattr(pipeline, "compress_video", fake_compress)
    monkeypatch.setattr(pipeline.scanner, "scan_assets", fake_scan)
    if sdk_version is None:
        monkeypatch.setattr(packager, "find_sdk", lambda *a, **kw: None)
    else:
        monkeypatch.setattr(packager, "find_sdk",
                            lambda *a, **kw: str(tmp_path / "sdk"))
        monkeypatch.setattr(packager, "sdk_version",
                            lambda *a, **kw: sdk_version)


def _run_project(tmp_path, monkeypatch, calls, av1: bool, name: str,
                 sdk_version=None, script_version=None):
    project = tmp_path / name
    game = project / "game"
    game.mkdir(parents=True)
    (game / "script.rpy").write_text("label start:\n    pass\n",
                                     encoding="utf-8")
    (game / "movie.webm").write_bytes(b"source-video" * 20)
    if script_version is not None:
        (game / "script_version.txt").write_text(script_version,
                                                 encoding="utf-8")
    _patch_video_pipeline(monkeypatch, tmp_path, calls, sdk_version)
    opts = OptimizeOptions(do_images=False, do_audio=False,
                           do_fonts=False, do_videos=True,
                           use_cache=True, experimental_av1=av1)
    result = pipeline.run_project(
        str(project), opts, str(tmp_path / f"{name}-work"),
        str(tmp_path / f"{name}-out"))
    return (Path(result["working_dir"]) / "game" / "movie.webm").read_bytes()


def test_video_cache_key_includes_av1_toggle(tmp_path, monkeypatch):
    """缓存键里的 av1 位必须反映最终生效值：放行与停用不能串用缓存。

    两边都给"引擎确定 >= 8.0"的前提（有版本文件 / 有 8.3 SDK），
    这样差异只来自用户开关本身，测的才是缓存键而不是门控。
    """
    monkeypatch.setenv("RENPYSLIM_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    calls = []

    assert _run_project(tmp_path, monkeypatch, calls, True, "with-av1",
                        script_version="(8, 3, 4)") == b"AV1"
    assert _run_project(tmp_path, monkeypatch, calls, False, "av1-disabled",
                        script_version="(8, 3, 4)") == b"VP9"
    assert calls == [True, False]


def test_video_cache_not_shared_when_av1_disabled_by_gate(tmp_path,
                                                          monkeypatch):
    """门控停用 AV1 时不能复用上次 AV1 的缓存产物。

    同一份源视频先按 AV1 编（有 8.3 SDK 兜底），再在无 SDK、无版本文件的
    工程里跑一次：门控必须把它降级成普通编码，缓存键随之变化，
    绝不能把 AV1 的缓存结果贴回来。
    """
    monkeypatch.setenv("RENPYSLIM_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    calls = []

    assert _run_project(tmp_path, monkeypatch, calls, True, "av1-on",
                        sdk_version="Ren'Py 8.3.4.2201") == b"AV1"
    assert _run_project(tmp_path, monkeypatch, calls, True, "av1-off") == b"VP9"
    assert calls == [True, False]
