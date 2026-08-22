"""2026-08-17 全面审查报告（AUDIT-2026-08-17.md）修复的回归测试。

每条对应报告中核实属实并已修复的缺陷，防止将来重构把坑再挖出来。
"""
import json
import pickle
import subprocess
import time
import zipfile
import zlib
from pathlib import Path

import pytest

from rtools import archives, cleanup, verifier
from rtools.config import OptimizeOptions
from rtools.models import ChangeRecord, Progress
from rtools.pipeline import PipelineCancelled, _run_jobs_or_flush
from rtools.utils import find_suffix_clashes


# ---------------------------------------------------------------------------
# 严重-1：成品瘦身无视用户的图片/音频/字体开关
# ---------------------------------------------------------------------------

def _make_jpg(path: Path, size=(720, 720)) -> None:
    import random
    from PIL import Image
    im = Image.new("RGB", size)
    px = im.load()
    rnd = random.Random(42)
    for x in range(size[0]):           # 渐变+噪声：体积够大且 q85 重压必然变小
        for y in range(size[1]):
            px[x, y] = (((x * 255) // size[0] + rnd.randint(0, 31)) % 256,
                        ((y * 255) // size[1] + rnd.randint(0, 31)) % 256,
                        (128 + rnd.randint(0, 31)) % 256)
    im.save(path, "JPEG", quality=98)


def test_dist_respects_image_switch(tmp_path):
    """关掉图片开关后，成品模式绝不能再动图片（曾全部失效）。"""
    from rtools.pipeline import run_dist

    dist = tmp_path / "Game-dist"
    (dist / "game").mkdir(parents=True)
    jpg = dist / "game" / "bg.jpg"
    _make_jpg(jpg)
    before = jpg.read_bytes()

    opts = OptimizeOptions()
    opts.do_images = False
    opts.use_cache = False
    r1 = run_dist(str(dist), opts, str(tmp_path / "work"), str(tmp_path / "out"))
    # 原件不动是设计，优化发生在工作副本：关开关后副本里的图必须原样
    wd1 = Path(r1["working_dir"])
    assert (wd1 / "game" / "bg.jpg").read_bytes() == before, \
        "图片开关被关闭但图片仍被处理"
    assert r1["saved_bytes"] == 0

    # 对照组：开关打开时副本里的图会被压（证明测试样本本身有效）
    opts2 = OptimizeOptions()
    opts2.use_cache = False
    r2 = run_dist(str(dist), opts2, str(tmp_path / "work2"),
                  str(tmp_path / "out2"))
    wd2 = Path(r2["working_dir"])
    assert (wd2 / "game" / "bg.jpg").read_bytes() != before
    assert r2["saved_bytes"] > 0


# ---------------------------------------------------------------------------
# 严重-2：zip 中文文件名 GBK→cp437 乱码
# ---------------------------------------------------------------------------

def _make_gbk_zip(zp: Path) -> None:
    """模拟国产工具：中文文件名按 GBK 编码且不置 UTF-8 标志。"""
    raw_name = "游戏目录/图片测试.png".encode("gbk").decode("cp437")
    with zipfile.ZipFile(zp, "w") as zf:
        info = zipfile.ZipInfo(raw_name)
        zf.writestr(info, b"fake-image-data")
        # zipfile 对非 ASCII 名会自动置 UTF-8 标志；改掉内存对象，
        # close 写中央目录时即为"未置标志"的真实乱码场景
        zf.filelist[-1].flag_bits &= ~0x800


def test_zip_gbk_chinese_names_repaired(tmp_path):
    zp = tmp_path / "gbk.zip"
    _make_gbk_zip(zp)
    dest = tmp_path / "out"
    archives.extract_archive(str(zp), str(dest))
    assert (dest / "游戏目录" / "图片测试.png").exists(), \
        "GBK 编码的中文文件名没有被还原"
    assert not list(dest.glob("*乱*"))  # 无乱码目录残留


def test_zip_ascii_names_untouched(tmp_path):
    """真西文名不受名字修复影响。"""
    zp = tmp_path / "ascii.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("plain/asset.txt", b"x")
    dest = tmp_path / "out"
    archives.extract_archive(str(zp), str(dest))
    assert (dest / "plain" / "asset.txt").exists()


# ---------------------------------------------------------------------------
# 高-1：lint 超时/异常分支缺 suspects 键致收尾 KeyError
# ---------------------------------------------------------------------------

def test_lint_all_branches_have_suspects(tmp_path, monkeypatch):
    # 分支一：SDK 不可用
    r = verifier.lint_project(str(tmp_path / "不存在"), "whatever")
    assert r["suspects"] == [] and r["ran"] is False

    # 分支二：超时
    fake_sdk = tmp_path / "sdk"
    fake_sdk.mkdir()
    (fake_sdk / "renpy.exe").write_bytes(b"fake")

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="renpy", timeout=1)

    monkeypatch.setattr(verifier, "run_quiet", raise_timeout)
    r = verifier.lint_project(str(fake_sdk), "proj")
    assert r["suspects"] == [] and "超时" in r["summary"]

    # 分支三：启动失败
    monkeypatch.setattr(verifier, "run_quiet",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("拒绝访问")))
    r = verifier.lint_project(str(fake_sdk), "proj")
    assert r["suspects"] == [] and r["ok"] is False


# ---------------------------------------------------------------------------
# 高-2：定时清理会删掉运行中的任务
# ---------------------------------------------------------------------------

def test_new_job_cleanup_keeps_running():
    from web import app as webapp
    with webapp.JOBS_LOCK:
        old_running = "aaaa0001"
        old_done = "aaaa0002"
        webapp.JOBS.clear()
        webapp.JOBS[old_running] = {
            "id": old_running, "kind": "optimize", "status": "running",
            "logs": [], "result": None, "error": None, "cancel": False,
            "created": time.time() - 99999,   # 远超 2 小时
        }
        webapp.JOBS[old_done] = {
            "id": old_done, "kind": "optimize", "status": "done",
            "logs": [], "result": {}, "error": None, "cancel": False,
            "created": time.time() - 99999,
        }
    webapp._new_job("analyze")
    with webapp.JOBS_LOCK:
        assert old_running in webapp.JOBS, "运行中的任务被清理删掉了"
        assert old_done not in webapp.JOBS, "陈旧已结束任务应被清理"
        webapp.JOBS.clear()


# ---------------------------------------------------------------------------
# 高-3 + 中-33：撞名预检要看得见"现存资源"（含封包内）
# ---------------------------------------------------------------------------

def test_clash_detects_existing_target():
    # a.wav 的转换目标 a.ogg 与现存资源同名 → 必须判为撞车
    clashes = find_suffix_clashes(["audio/a.wav"], ".ogg",
                                  existing=["audio/a.ogg", "audio/b.ogg"])
    assert clashes == {"audio/a.ogg"}
    # 无 existing 时旧行为不变
    assert find_suffix_clashes(["audio/a.wav"], ".ogg") == set()
    # 源互撞依旧能检出
    assert find_suffix_clashes(["i/a.png", "i/a.jpg"], ".webp") == {"i/a.webp"}


def test_tmp_names_unique():
    """优化器 tmp 名带随机后缀（防并行任务共用固定 tmp 互踩）。"""
    from rtools import audio_optimizer, image_optimizer
    # 直接检查实现：同名调用两次生成的 tmp 名应不同（通过源码模式验证）
    src = Path(image_optimizer.__file__).read_text(encoding="utf-8")
    assert "uuid" in src and ".rtools." in src
    src2 = Path(audio_optimizer.__file__).read_text(encoding="utf-8")
    assert "uuid" in src2 and ".rtools." in src2


# ---------------------------------------------------------------------------
# 高-4：多成品压缩包只处理一个平台，其余静默丢弃
# ---------------------------------------------------------------------------

def test_find_dist_roots_returns_all(tmp_path):
    ext = tmp_path / "ext"
    (ext / "PC版" / "game" / "scripts").mkdir(parents=True)
    (ext / "Mac版" / "game" / "gui").mkdir(parents=True)
    roots = archives.find_dist_roots(str(ext))
    assert len(roots) == 2
    # 单成品场景兼容入口不受影响
    single = tmp_path / "single"
    (single / "game").mkdir(parents=True)
    assert archives.find_dist_root(str(single)) == str(single)


def test_dist_smart_rejects_multi_platform_zip(tmp_path):
    from rtools.pipeline import PipelineError, run_dist_smart
    src = tmp_path / "pack_src"
    (src / "PC版" / "game").mkdir(parents=True)
    (src / "PC版" / "renpy.exe").write_bytes(b"x")
    (src / "PC版" / "game" / "options.rpyc").write_bytes(b"x")
    (src / "Mac版" / "game").mkdir(parents=True)
    (src / "Mac版" / "game" / "options.rpyc").write_bytes(b"x")
    zp = tmp_path / "三合一.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        for p in src.rglob("*"):
            if p.is_file():
                zf.write(p, Path("pack_src") / p.relative_to(src))
    with pytest.raises(PipelineError, match="拆开"):
        run_dist_smart(str(zp), OptimizeOptions(),
                       str(tmp_path / "work"), str(tmp_path / "out"))


# ---------------------------------------------------------------------------
# 高-5 + 中-1：中断兜底落清单；取消时聚合本批已完成改动
# ---------------------------------------------------------------------------

def _patch_run_jobs(monkeypatch, exc):
    import rtools.pipeline as pl

    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(pl, "_run_jobs", boom)


def test_crash_flushes_partial_changelog(tmp_path, monkeypatch):
    """非取消的真异常（如磁盘满）也必须落部分清单。"""
    _patch_run_jobs(monkeypatch, OSError("磁盘已满"))
    with pytest.raises(OSError):
        _run_jobs_or_flush(Progress(), "optimize", [("j", lambda: None)],
                           None, str(tmp_path), [], 0)
    data = json.loads((tmp_path / "changelog.json").read_text(encoding="utf-8"))
    assert data["cancelled"] is True


def test_cancel_aggregates_completed_results(tmp_path, monkeypatch):
    """取消时本批已完成的改动要进 records 和 saved_bytes。"""
    rec = ChangeRecord(action="compress", src="a.png", detail="x")
    _patch_run_jobs(monkeypatch, PipelineCancelled(
        [{"records": [rec], "saved": 5}]))
    records: list = []
    with pytest.raises(PipelineCancelled):
        _run_jobs_or_flush(Progress(), "optimize", [("j", lambda: None)],
                           None, str(tmp_path), records, 10)
    assert len(records) == 1 and records[0].src == "a.png"
    data = json.loads((tmp_path / "changelog.json").read_text(encoding="utf-8"))
    assert data["saved_bytes"] == 15          # 10 + 本批 5
    assert len(data["records"]) == 1


# ---------------------------------------------------------------------------
# 中-4 / 中-9 / 中-10：缓存原子复制、备份原子写、自映射防重编码
# ---------------------------------------------------------------------------

def test_apply_cached_atomic_and_cleanup(tmp_path):
    from rtools import cache
    src = tmp_path / "cached.bin"
    src.write_bytes(b"cached-content")
    dst = tmp_path / "deep" / "target.bin"
    assert cache.apply_cached(str(src), str(dst)) is True
    assert dst.read_bytes() == b"cached-content"
    # 失败路径：不留 tmp 垃圾
    assert cache.apply_cached(str(tmp_path / "不存在"), str(dst)) is False
    leftovers = [p for p in tmp_path.rglob("*") if ".tmp" in p.name]
    assert leftovers == []


def test_backup_zip_no_tmp_residue(tmp_path):
    from rtools import backup
    target = tmp_path / "Proj"
    (target / "game").mkdir(parents=True)
    (target / "game" / "a.rpy").write_text("x", encoding="utf-8")
    out = backup.make_backup_zip(str(target), str(tmp_path / "bak.zip"))
    assert Path(out).exists()
    assert not list(tmp_path.glob("*.tmp*"))


def test_store_self_prevents_reprocess(tmp_path, monkeypatch):
    from rtools import cache
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    f = tmp_path / "asset.jpg"
    f.write_bytes(b"processed-output-bytes")
    cache.store_self(str(f), "img|q85|same")
    h = cache.hash_file(str(f))
    assert cache.lookup_hash(h, "img|q85|same") is not None


# ---------------------------------------------------------------------------
# 中-8：clean_junk 曾对任意层级 saves/cache 无差别整删
# ---------------------------------------------------------------------------

def test_clean_junk_only_known_safe_paths(tmp_path):
    root = tmp_path / "dist"
    # 已知安全位置：应删
    (root / "saves").mkdir(parents=True)
    (root / "saves" / "1.save").write_bytes(b"x")
    (root / "game" / "cache").mkdir(parents=True)
    (root / "game" / "cache" / "bytecode.rpyb").write_bytes(b"x")
    # 任意层级同名目录：绝不能删（第三方游戏可能存必需数据）
    (root / "game" / "mods" / "cache").mkdir(parents=True)
    keep = root / "game" / "mods" / "cache" / "needed.dat"
    keep.write_bytes(b"important")

    res = cleanup.clean_junk(str(root))
    assert not (root / "saves").exists()
    assert not (root / "game" / "cache").exists()
    assert keep.exists(), "任意层级的 cache 目录被误删"
    assert any("saves/" in r for r in res["removed"])


# ---------------------------------------------------------------------------
# 中-12 / 中-13：RPA 脏索引必须转 RpaError 而非裸 TypeError；长度校验
# ---------------------------------------------------------------------------

def _write_rpa(path: Path, index: dict, version="RPA-3.0",
               key=0x42424242) -> None:
    blob = zlib.compress(pickle.dumps(index, 2))
    if version == "RPA-3.0":
        header = b"RPA-3.0 %016x %08x\n" % (64, key)
    else:
        header = b"RPA-2.0 %016x\n" % 64
    data = header + b" " * (64 - len(header)) + blob
    path.write_bytes(data)


def test_rpa_dirty_index_raises_rpaerror(tmp_path):
    from rtools import rpa
    bad = tmp_path / "bad.rpa"
    # value 是 int：迭代它直接 TypeError（旧版会逃出容错）
    _write_rpa(bad, {"a.txt": 12345})
    with pytest.raises(rpa.RpaError):
        rpa.RpaArchive(str(bad))


def test_rpa_length_lt_prefix_rejected(tmp_path):
    from rtools import rpa
    bad = tmp_path / "bad2.rpa"
    # RPA-2.0（不异或）：length=5 小于 prefix 长 25 → 负数 read 语义
    _write_rpa(bad, {"a.txt": [(59, 5, b"x" * 25)]}, version="RPA-2.0")
    with pytest.raises(rpa.RpaError):
        rpa.RpaArchive(str(bad))


# ---------------------------------------------------------------------------
# 中-21：lint 解码回退 cp936 + 错误行判定
# ---------------------------------------------------------------------------

def test_lint_detects_error_lines_without_error_word(tmp_path, monkeypatch):
    """典型错误行不含 error 一词，退出码 0 也得判不通过。"""
    fake_sdk = tmp_path / "sdk"
    fake_sdk.mkdir()
    (fake_sdk / "renpy.exe").write_bytes(b"fake")
    out = ("game/script.rpy:41: say statement expects a string\n"
           "Ren'Py lint finished at 12:00:00.12\n").encode("utf-8")

    class _P:
        returncode = 0
        stdout = out
        stderr = b""

    monkeypatch.setattr(verifier, "run_quiet", lambda *a, **k: _P())
    r = verifier.lint_project(str(fake_sdk), "proj")
    assert r["ok"] is False
    assert len(r["suspects"]) == 1
    assert "script.rpy:41" in r["suspects"][0]


def test_lint_gbk_output_decoded(tmp_path, monkeypatch):
    """中文 Windows 管道输出是 GBK，不得解成乱码。"""
    fake_sdk = tmp_path / "sdk"
    fake_sdk.mkdir()
    (fake_sdk / "renpy.exe").write_bytes(b"fake")
    out = "game/script.rpy:3: 错误：找不到图片\n".encode("gbk")

    class _P:
        returncode = 1
        stdout = out
        stderr = b""

    monkeypatch.setattr(verifier, "run_quiet", lambda *a, **k: _P())
    r = verifier.lint_project(str(fake_sdk), "proj")
    assert "错误" in r["output"]           # 不是乱码
    assert r["ok"] is False


# ---------------------------------------------------------------------------
# 中-22：引用改写右守卫缺失导致前缀重叠文件名断链
# ---------------------------------------------------------------------------

def test_refs_right_guard(tmp_path):
    from rtools.refs import RefIndex
    game = tmp_path / "game"
    game.mkdir()
    (game / "script.rpy").write_text(
        "show bg.png.png\nshow bg.png@2x\nshow bg.png\n", encoding="utf-8")
    idx = RefIndex(str(game))
    hits = idx.find("bg.png")
    assert [ln for _, ln in hits] == [3], "前缀重叠的写法被误判为引用"

    idx.rewrite({"bg.png": "bg.webp"})
    text = (game / "script.rpy").read_text(encoding="utf-8")
    assert "bg.png.png" in text, "bg.png.png 被误改"
    assert "bg.png@2x" in text, "bg.png@2x 被误改"
    assert "show bg.webp\n" in text


# ---------------------------------------------------------------------------
# 中-17：外部程序调用必须隔离 stdin（加密 RAR 无密码不再挂死）
# ---------------------------------------------------------------------------

def test_run_quiet_isolates_stdin():
    from rtools.procutil import run_quiet
    import inspect
    src = inspect.getsource(run_quiet)
    assert "DEVNULL" in src


# ---------------------------------------------------------------------------
# 中-18：WinZip AES 明确报不支持
# ---------------------------------------------------------------------------

def test_zip_aes_reports_unsupported(tmp_path):
    """WinZip AES（compress_type=99）必须明说不支持，而非误报损坏。"""
    zp = tmp_path / "aes.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("a.txt", b"x")
    # 二进制补丁：把 local header 与 central directory 的
    # compress_type 字段改成 99（标准库造不出 AES 条目）
    data = bytearray(zp.read_bytes())
    loc = data.find(b"\x50\x4b\x03\x04")
    data[loc + 8:loc + 10] = (99).to_bytes(2, "little")
    cen = data.find(b"\x50\x4b\x01\x02")
    data[cen + 10:cen + 12] = (99).to_bytes(2, "little")
    zp.write_bytes(bytes(data))
    assert zipfile.ZipFile(zp).infolist()[0].compress_type == 99
    with pytest.raises(archives.ArchiveError, match="AES"):
        archives.extract_archive(str(zp), str(tmp_path / "out"))


# ---------------------------------------------------------------------------
# 多核放开（2026-08-17 用户拍板）：并行度随核心数扩展，不再写死 6
# ---------------------------------------------------------------------------

def test_worker_count_scales_with_cores(monkeypatch):
    import rtools.pipeline as pl
    # 28 核机器：旧版只给 6 路，现在应给到 16（上限）
    monkeypatch.setattr(pl.os, "cpu_count", lambda: 28)
    assert pl._worker_count(10) == 16
    # 8 核：核心数减 2
    monkeypatch.setattr(pl.os, "cpu_count", lambda: 8)
    assert pl._worker_count(10) == 6
    # 4 核小机器：保底 2 路
    monkeypatch.setattr(pl.os, "cpu_count", lambda: 4)
    assert pl._worker_count(10) == 2
    # 小批量照旧串行（不值得起线程池）
    assert pl._worker_count(3) == 1


def test_video_threads_scale_with_cores():
    from rtools import video_optimizer
    # 视频线程数应在 [2, 16] 区间且随核心数变化（模块加载时计算）
    assert 2 <= video_optimizer._V_THREADS <= 16


# ---------------------------------------------------------------------------
# 默认档画质优先 + 小文件也要榨（2026-08-17 用户拍板）
# ---------------------------------------------------------------------------

def test_default_preset_is_quality_first():
    from rtools.config import PRESETS, DEFAULT_PRESET
    assert DEFAULT_PRESET == "conservative"
    p = PRESETS[DEFAULT_PRESET]
    assert p.image_quality >= 95        # 画质优先：近视觉无损参数
    assert p.png_to_webp is True        # WebP q95 同样近无损但体积更小


def test_small_files_no_longer_skipped():
    """体积门槛降到极低：几十 KB 的小图转 WebP 后只剩几 KB，
    数量又多，不能再被门槛挡在门外。"""
    from rtools.config import PRESETS
    assert all(v.min_size_kb <= 2 for v in PRESETS.values())


# ---------------------------------------------------------------------------
# 视频同编码安全重编 + AV1 选项（2026-08-17 官方文档研究后落地）
# 依据：renpy.org/doc/html/movie.html——官方支持 AV1/VP9/VP8/Theora/
# MPEG-1/2/4p2，明确不支持 H.264 解码（和 AAC）
# ---------------------------------------------------------------------------

def _mock_codec(monkeypatch, codec):
    import rtools.video_optimizer as vo
    monkeypatch.setattr(vo, "probe_video_codec", lambda p: codec)


def test_mp4_non_h264_refused(tmp_path, monkeypatch):
    """官方不支持 H.264 解码：非 H.264 的 mp4（如 HEVC）绝不能
    转成 H.264——那会把原本能放的变成放不出来。
    第二波：拒绝从抛 RuntimeError 改为归 skipped（三态），
    流水线按“格式不适合”记账而不是当成失败。"""
    import rtools.video_optimizer as vo
    _mock_codec(monkeypatch, "hevc")
    monkeypatch.setattr(vo, "find_ffmpeg", lambda: "fake-ffmpeg")
    src = tmp_path / "v.mp4"
    src.write_bytes(b"x")
    res = vo.compress_video(str(src), str(src))
    assert not res
    assert res["status"] == "skipped"
    assert "保留原文件" in res["reason"]


def test_mp4_h264_allowed(tmp_path, monkeypatch):
    """原本就是 H.264 的 mp4（游戏既然带着它，说明能放）按 H.264
    重编不新增风险——不拦截，交给 ffmpeg 实际处理。"""
    import rtools.video_optimizer as vo
    _mock_codec(monkeypatch, "h264")
    monkeypatch.setattr(vo, "find_ffmpeg", lambda: None)
    # 没有 ffmpeg 时报"找不到 FFmpeg"而非"保留原文件"，证明决策层放行了
    src = tmp_path / "v.mp4"
    src.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="FFmpeg"):
        vo.compress_video(str(src), str(src))


def test_webm_unsafe_codec_refused(tmp_path, monkeypatch):
    """第二波：同改归 skipped（三态）；另验证探测返回 None（编码未知）
    时不再盲编，同样保守拒绝。"""
    import rtools.video_optimizer as vo
    _mock_codec(monkeypatch, "h264")   # webm 容器装 h264：不在官方清单
    monkeypatch.setattr(vo, "find_ffmpeg", lambda: "fake-ffmpeg")
    src = tmp_path / "v.webm"
    src.write_bytes(b"x")
    res = vo.compress_video(str(src), str(src))
    assert not res
    assert res["status"] == "skipped"
    assert "保留原文件" in res["reason"]
    # 探测未知（None）也拒绝，不猜不盲编（第二波修复）
    _mock_codec(monkeypatch, None)
    res2 = vo.compress_video(str(src), str(src))
    assert not res2 and res2["status"] == "skipped"
    assert "未知" in res2["reason"]


def test_av1_option_wired():
    """AV1 实验选项全链路接通：配置 → 签名。"""
    import inspect
    from rtools.config import OptimizeOptions
    from rtools.video_optimizer import compress_video
    opts = OptimizeOptions()
    assert opts.experimental_av1 is False      # 默认关（实验性）
    assert "use_av1" in inspect.signature(compress_video).parameters


# ---------------------------------------------------------------------------
# 反编译解锁 + 包回 rpa（2026-08-17 用户拍板，unrpyc vendored MIT）
# ---------------------------------------------------------------------------

def test_rpa_rebuild_rename_replaces_entry(tmp_path):
    """改名替换重建：旧条目剔除、新名入包（格式转换后包回 rpa用）。"""
    from rtools import rpa
    src = tmp_path / "a.rpa"
    w = rpa.RpaWriter(str(src))
    w.add("images/old.png", b"old-bytes")
    w.add("keep.txt", b"keep")
    w.close()

    new_file = tmp_path / "new.webp"
    new_file.write_bytes(b"new-bytes")
    dest = tmp_path / "b.rpa"
    replaced, total = rpa.rebuild_archive(
        str(src), str(dest),
        {"images/old.png": ("images/new.webp", str(new_file))})
    assert (replaced, total) == (1, 2)

    arc = rpa.RpaArchive(str(dest))
    names = arc.names()
    assert "images/old.png" not in names, "旧条目应被剔除"
    assert "images/new.webp" in names, "新名应入包"
    assert arc.read("images/new.webp") == b"new-bytes"
    assert arc.read("keep.txt") == b"keep"   # 未替换的原样保留
    arc.close()


def test_decompile_roundtrip(tmp_path):
    """rpy 编译 → 删源 → 反编译找回，引用字符串完好（需本机 SDK）。"""
    import subprocess
    from rtools import packager
    from rtools.decompile import decompile_scripts
    sdk = packager.find_sdk()
    if not sdk:
        pytest.skip("本机无 Ren'Py SDK")
    proj = tmp_path / "DecProj"
    (proj / "game").mkdir(parents=True)
    src = proj / "game" / "script.rpy"
    src.write_text('image bg_x = "images/bg/x.png"\nlabel start:\n    "hi"\n',
                   encoding="utf-8")
    subprocess.run([str(Path(sdk) / "renpy.exe"), str(proj), "compile"],
                   capture_output=True, timeout=300, cwd=sdk)
    assert (proj / "game" / "script.rpyc").exists()
    src.unlink()                       # 模拟无源码成品

    stats = decompile_scripts(str(proj / "game"))
    assert stats["decompiled"] == 1 and not stats["failed"]
    text = src.read_text(encoding="utf-8")
    assert "images/bg/x.png" in text   # 引用字符串完好，可被改写

    # 再跑一遍：已有 rpy 应跳过不覆盖
    stats2 = decompile_scripts(str(proj / "game"))
    assert stats2["skipped"] == 1 and stats2["decompiled"] == 0
