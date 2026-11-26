"""2026-11-27 交叉审计修复的回归测试。

覆盖：只读格式回包降级、相对 SDK 路径、tar setuid 位、POSIX zip
大小写撞名、取消的跨任务隔离、任务历史跨进程写、工作副本符号链接
与嵌套目标、交付目录不删除既有目录、build-tools 可执行性、APK remap
预检、CLI 打包失败退出码。
"""
from __future__ import annotations

import json
import os
import stat
import struct
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
import zlib
from pathlib import Path

import pytest

from rtools import apk, archives, backup, jobstore, pipeline, procutil, remap, sdk

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 压缩包
# ---------------------------------------------------------------------------

def test_repack_suffix_downgrades_readonly_formats():
    assert archives.repack_suffix(".zip") == ".zip"
    assert archives.repack_suffix(".tar.bz2") == ".tar.bz2"
    assert archives.repack_suffix(".tgz") == ".tgz"
    assert archives.repack_suffix(".7z") == ".zip"
    assert archives.repack_suffix(".rar") == ".zip"
    assert archives.repack_suffix("") == ".zip"


@pytest.mark.skipif(os.name == "nt", reason="setuid 语义仅 POSIX")
def test_tar_extraction_strips_setuid_bits(tmp_path):
    tar_path = tmp_path / "evil.tar"
    payload = tmp_path / "payload.sh"
    payload.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    info = tarfile.TarInfo("game/run.sh")
    info.size = payload.stat().st_size
    info.mode = 0o4755
    with tarfile.open(tar_path, "w") as tf:
        with payload.open("rb") as fh:
            tf.addfile(info, fh)
    dest = tmp_path / "out"
    archives.extract_archive(str(tar_path), str(dest))
    mode = stat.S_IMODE((dest / "game" / "run.sh").stat().st_mode)
    assert mode & 0o7000 == 0
    assert mode & 0o111


@pytest.mark.skipif(os.name == "nt", reason="POSIX 大小写敏感")
def test_zip_case_variants_are_not_renamed_on_posix(tmp_path):
    zpath = tmp_path / "case.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("game/Logo.png", b"upper")
        zf.writestr("game/logo.png", b"lower")
    dest = tmp_path / "out"
    archives.extract_archive(str(zpath), str(dest))
    assert (dest / "game" / "Logo.png").read_bytes() == b"upper"
    assert (dest / "game" / "logo.png").read_bytes() == b"lower"
    assert not list((dest / "game").glob("*.dup*"))


# ---------------------------------------------------------------------------
# SDK 路径
# ---------------------------------------------------------------------------

def test_relative_sdk_is_resolved_to_absolute(tmp_path, monkeypatch):
    sdk_dir = tmp_path / "sdk"
    sdk_dir.mkdir()
    launcher = sdk_dir / "renpy.sh"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    found = sdk.find_launcher("sdk")
    assert found is not None
    assert os.path.isabs(found)
    assert Path(found) == launcher.resolve()
    assert sdk.require_launcher("sdk") == found


# ---------------------------------------------------------------------------
# 取消的任务隔离
# ---------------------------------------------------------------------------

def _spawn_scoped_sleeper(holder: dict, key: str, ready: threading.Event,
                          errors: list):
    def run():
        try:
            with procutil.task_scope() as token:
                holder[key] = token
                ready.set()
                procutil.run_quiet(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    capture_output=True)
        except Exception as exc:            # noqa: BLE001 - 记录被杀的证据
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _wait_active(count: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with procutil._ACTIVE_LOCK:
            if len(procutil._ACTIVE) >= count:
                return
        time.sleep(0.02)
    raise AssertionError("外部进程未登记到 _ACTIVE")


@pytest.mark.skipif(os.name == "nt", reason="依赖 POSIX 进程组语义")
def test_kill_children_only_touches_same_scope():
    holder: dict = {}
    errors: list = []
    ready_a = threading.Event()
    ready_b = threading.Event()
    thread_a = _spawn_scoped_sleeper(holder, "a", ready_a, errors)
    assert ready_a.wait(10)
    _wait_active(1)
    thread_b = _spawn_scoped_sleeper(holder, "b", ready_b, errors)
    assert ready_b.wait(10)
    _wait_active(2)

    procutil.kill_children(scope=holder["a"])
    thread_a.join(timeout=15)
    assert not thread_a.is_alive(), "被取消任务的子进程没有被杀掉"
    time.sleep(0.3)
    assert thread_b.is_alive(), "另一个任务的外部进程被误杀"

    procutil.kill_children()
    thread_b.join(timeout=15)
    assert not thread_b.is_alive()


# ---------------------------------------------------------------------------
# 任务历史跨进程
# ---------------------------------------------------------------------------

def test_jobstore_cross_process_writes_are_not_lost(tmp_path):
    env = dict(os.environ)
    env["RENPYSLIM_HOME"] = str(tmp_path)
    env["PYTHONPATH"] = str(REPO)
    script = (
        "import os\n"
        "from rtools import jobstore\n"
        "for i in range(15):\n"
        "    jobstore.record_start(f'p{os.getpid()}-{i}', 'test', 't')\n"
    )
    procs = [subprocess.Popen([sys.executable, "-c", script], env=env,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
             for _ in range(2)]
    assert all(p.wait(timeout=120) == 0 for p in procs)

    jobs = jobstore.list_jobs(limit=jobstore.MAX_JOBS)
    assert len(jobs) == 30, f"并发写丢了记录，只剩 {len(jobs)} 条"


# ---------------------------------------------------------------------------
# 工作副本与交付目录
# ---------------------------------------------------------------------------

def test_working_copy_rejects_destination_inside_project(tmp_path):
    project = tmp_path / "Game"
    (project / "game").mkdir(parents=True)
    with pytest.raises(backup.BackupError):
        backup.make_working_copy(str(project), str(project / "work"))


def test_working_copy_skips_symlinks(tmp_path):
    project = tmp_path / "Game"
    (project / "game").mkdir(parents=True)
    (project / "game" / "script.rpy").write_text("label start:\n", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("TOP-SECRET", encoding="utf-8")
    link = project / "game" / "leak.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不允许创建符号链接")

    dest = backup.make_working_copy(str(project), str(tmp_path / "work"))
    copied = Path(dest) / "game" / "leak.txt"
    assert not copied.exists(), "符号链接被跟随，工程外文件进了工作副本"


def test_unique_dir_never_reuses_existing_directory(tmp_path):
    existing = tmp_path / "Game"
    existing.mkdir()
    (existing / "important.txt").write_text("keep", encoding="utf-8")
    chosen = pipeline._unique_dir(existing)
    assert chosen != existing
    assert not chosen.exists()
    assert (existing / "important.txt").read_text(encoding="utf-8") == "keep"


def test_unreferenced_scan_includes_source_scripts(tmp_path):
    """带源码的成品：rpy 里明确引用的资源不能被判成无引用。"""
    from rtools.models import AssetInfo, AssetKind

    game = tmp_path / "game"
    (game / "audio").mkdir(parents=True)
    (game / "audio" / "used.ogg").write_bytes(b"OggS")
    (game / "audio" / "orphan.ogg").write_bytes(b"OggS")
    (game / "script.rpy").write_text(
        'play music "audio/used.ogg"\n', encoding="utf-8")
    (game / "other.rpyc").write_bytes(b"not a real rpyc")

    loose = [
        AssetInfo(path=str(game / "audio" / "used.ogg"),
                  rel="game/audio/used.ogg", kind=AssetKind.AUDIO, size=4),
        AssetInfo(path=str(game / "audio" / "orphan.ogg"),
                  rel="game/audio/orphan.ogg", kind=AssetKind.AUDIO, size=4),
    ]
    flagged = pipeline._find_unreferenced(tmp_path, loose)
    assert "game/audio/used.ogg" not in flagged
    assert "game/audio/orphan.ogg" in flagged


def test_av1_requires_engine_8(tmp_path):
    assert not pipeline._av1_allowed(tmp_path)          # 缺版本文件
    (tmp_path / "script_version.txt").write_text("(7, 4, 0)", encoding="utf-8")
    assert not pipeline._av1_allowed(tmp_path)
    (tmp_path / "script_version.txt").write_text("(8, 0, 0)", encoding="utf-8")
    assert pipeline._av1_allowed(tmp_path)
    (tmp_path / "script_version.txt").write_text("(8, 3, 2)", encoding="utf-8")
    assert pipeline._av1_allowed(tmp_path)


def test_analyze_rejects_multi_root_archive(tmp_path, capsys):
    import argparse
    import cli

    z = tmp_path / "multi.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for platform in ("PC", "Mac"):
            zf.writestr(f"{platform}/Game/game/script.rpyc", b"fake")
            zf.writestr(f"{platform}/Game/game/gui/logo.png", b"png")
    code = cli.cmd_analyze(argparse.Namespace(path=str(z), mode=None,
                                              password=None))
    payload = json.loads(capsys.readouterr().out)
    assert code == 1 and payload["ok"] is False
    assert "2 个成品目录" in payload["error"]


def test_archive_config_restores_user_file(tmp_path):
    from rtools import packager

    project = tmp_path / "Game"
    game = project / "game"
    game.mkdir(parents=True)
    user_cfg = game / packager.ARCHIVE_CONFIG_NAME
    original = "init python:\n    build.archive('custom', 'all')\n"
    user_cfg.write_text(original, encoding="utf-8")

    cfg, backed_up = packager.inject_archive_config(str(project))
    assert backed_up is True
    assert Path(cfg).read_text(encoding="utf-8") != original
    assert user_cfg.with_name(
        user_cfg.name + packager.USER_BACKUP_SUFFIX).exists()

    packager.remove_archive_config(str(project))
    assert user_cfg.exists(), "用户原有配置被删掉了"
    assert user_cfg.read_text(encoding="utf-8") == original
    assert not user_cfg.with_name(
        user_cfg.name + packager.USER_BACKUP_SUFFIX).exists()


# ---------------------------------------------------------------------------
# APK
# ---------------------------------------------------------------------------

def _make_rpyc2(text: str) -> bytes:
    """构造最小 RPYC2：头 + 槽位表 + zlib 压缩文本槽（与官方格式一致）。"""
    payload = zlib.compress(text.encode("utf-8"))
    slot1 = struct.pack("III", 1, 34, len(payload))
    slot0 = struct.pack("III", 0, 0, 0)
    return b"RENPY RPC2" + slot1 + slot0 + payload


@pytest.mark.skipif(os.name == "nt", reason="执行位语义仅 POSIX")
def test_find_build_tools_skips_non_executable_latest(tmp_path):
    bt = tmp_path / "rapt" / "Sdk" / "build-tools"
    for version, mode in (("35.0.0", 0o644), ("34.0.0", 0o755)):
        d = bt / version
        d.mkdir(parents=True)
        for name in ("zipalign", "apksigner"):
            tool = d / name
            tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            tool.chmod(mode)
    zipalign, signer = apk.find_build_tools(str(tmp_path))
    assert zipalign is not None and signer is not None
    assert "34.0.0" in zipalign, "选中了不可执行的最新版工具"
    assert "34.0.0" in signer


def test_apk_remap_support_rejects_old_engine(tmp_path):
    root = tmp_path / "x-game"
    root.mkdir()
    (root / "x-script_version.txt").write_text("(7, 4, 0)", encoding="utf-8")
    ok, reason = apk._check_apk_remap_support(root)
    assert not ok and "8.0.0" in reason


def test_apk_remap_support_rejects_compiled_callback(tmp_path):
    root = tmp_path / "x-game"
    root.mkdir()
    (root / "x-script_version.txt").write_text("(8, 1, 0)", encoding="utf-8")
    (root / "x-scripts").mkdir()
    (root / "x-scripts" / "x-extra.rpyc").write_bytes(
        _make_rpyc2("config.file_open_callback = custom_loader\n"))
    ok, reason = apk._check_apk_remap_support(root)
    assert not ok and "回调" in reason


def test_remap_support_rejects_compressed_rpyc_callback(tmp_path):
    """真实 rpyc 文本在 zlib 槽里：必须解压后再找特征串。"""
    (tmp_path / "script_version.txt").write_text("(8, 1, 0)", encoding="utf-8")
    (tmp_path / "script.rpyc").write_bytes(
        _make_rpyc2("label start:\n    pass\n"
                    "config.file_open_callback = custom_loader\n"))
    ok, reason = remap.check_remap_support(tmp_path)
    assert not ok and "回调" in reason


def test_remap_support_accepts_clean_compressed_rpyc(tmp_path):
    (tmp_path / "script_version.txt").write_text("(8, 1, 0)", encoding="utf-8")
    (tmp_path / "script.rpyc").write_bytes(
        _make_rpyc2("label start:\n    'hi'\n"))
    ok, reason = remap.check_remap_support(tmp_path)
    assert ok and reason == ""


def test_apk_remap_support_accepts_clean_package(tmp_path):
    root = tmp_path / "x-game"
    root.mkdir()
    (root / "x-script_version.txt").write_text("(8, 1, 0)", encoding="utf-8")
    (root / "x-scripts").mkdir()
    (root / "x-scripts" / "x-script.rpyc").write_bytes(b"RENPY RPC2\x00label start")
    ok, reason = apk._check_apk_remap_support(root)
    assert ok and reason == ""


# ---------------------------------------------------------------------------
# CLI 打包失败契约
# ---------------------------------------------------------------------------

def test_cli_package_reports_failure_when_sdk_packaging_fails(
        monkeypatch, capsys):
    import argparse
    import cli

    monkeypatch.setattr(cli.packager, "find_sdk", lambda _arg: "/tmp/fake-sdk")
    monkeypatch.setattr(
        cli.packager, "package_project",
        lambda *a, **k: {"built": [], "errors": ["官方打包退出码 1"],
                         "destination": "/tmp/out", "artifacts": []})
    args = argparse.Namespace(platforms="pc", sdk=None, path="/tmp/Game",
                              destination=None, archive_rpa=False)
    code = cli.cmd_package(args)
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert "打包失败" in payload["error"]


def test_cli_package_partial_platform_failure_is_not_success(monkeypatch, capsys):
    """pc 成功、android 失败：任一所请求平台失败都必须 ok:false。"""
    import argparse
    import cli

    monkeypatch.setattr(cli.packager, "find_sdk", lambda _arg: "/tmp/fake-sdk")
    monkeypatch.setattr(
        cli.packager, "package_project",
        lambda *a, **k: {"built": ["pc"], "errors": ["安卓打包失败（退出码 7）"],
                         "destination": "/tmp/out", "artifacts": []})
    args = argparse.Namespace(platforms="pc,android", sdk=None,
                              path="/tmp/Game", destination=None,
                              archive_rpa=False)
    code = cli.cmd_package(args)
    payload = json.loads(capsys.readouterr().out)
    assert code == 1 and payload["ok"] is False
    assert "安卓" in payload["error"] and "pc" in payload["error"]


def test_analyze_archive_forces_dist_mode(tmp_path, capsys):
    """压缩包一律按成品分析：自动与显式 project 都必须落 dist。"""
    import argparse
    import cli

    z = tmp_path / "source.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Game/game/script.rpy", "label start:\n    'hi'\n")

    code = cli.cmd_analyze(argparse.Namespace(path=str(z), mode=None,
                                              password=None))
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["report"]["mode"] == "dist"

    code = cli.cmd_analyze(argparse.Namespace(path=str(z), mode="project",
                                              password=None))
    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out)["report"]["mode"] == "dist"
    assert "压缩包" in captured.err and "dist" in captured.err


# ---------------------------------------------------------------------------
# 反编译降级不得污染 stdout
# ---------------------------------------------------------------------------

def test_decompile_import_keeps_stdout_clean():
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from rtools import decompile\n"
        "decompile._import_unrpyc()\n"
        "print('OUT_OK')\n" % str(REPO)
    )
    proc = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip() == "OUT_OK", "导入期输出污染了 stdout"
