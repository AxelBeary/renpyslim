"""视频缓存必须区分 AV1 开关，避免复用编码格式不同的产物。"""
from pathlib import Path

from rtools import cache, packager, pipeline
from rtools.config import OptimizeOptions
from rtools.image_optimizer import OptimizeResult
from rtools.models import AssetInfo, AssetKind


def test_video_cache_key_includes_av1_toggle(tmp_path, monkeypatch):
    monkeypatch.setenv("RENPYSLIM_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    calls = []

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
    monkeypatch.setattr(packager, "find_sdk", lambda: None)
    monkeypatch.setattr(pipeline.scanner, "scan_assets", fake_scan)

    def run(av1: bool, name: str) -> bytes:
        project = tmp_path / name
        game = project / "game"
        game.mkdir(parents=True)
        (game / "script.rpy").write_text(
            "label start:\n    pass\n", encoding="utf-8")
        (game / "movie.webm").write_bytes(b"source-video" * 20)
        opts = OptimizeOptions(do_images=False, do_audio=False,
                               do_fonts=False, do_videos=True,
                               use_cache=True, experimental_av1=av1)
        result = pipeline.run_project(
            str(project), opts, str(tmp_path / f"{name}-work"),
            str(tmp_path / f"{name}-out"))
        return (Path(result["working_dir"]) / "game" / "movie.webm").read_bytes()

    assert run(True, "with-av1") == b"AV1"
    assert run(False, "av1-disabled") == b"VP9"
    assert calls == [True, False]
