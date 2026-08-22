"""评审收口修复（B 路）的集成测试（2026-08-23）。

覆盖四个评审点名的缺口：
1. remap 接线集成：低版本降级文案、坏注入脚本中止文案、
   注入阶段复查失败时"映射未写入请勿发布"报警并计失败；
2. 同名 rpa 两份时跳过重建并告警；
3. 超时杀进程树：run_quiet 超时抛 TimeoutExpired 且不挂起；
4. decompile 祖先目录撞名（项目位于 .../cache/... 下）不误伤脚本。
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from rtools import remap as remap_mod
from rtools.config import OptimizeOptions
from rtools.pipeline import run_dist


def _noise_png(path: Path, size: int = 320) -> None:
    """生成一张随机像素大 PNG（难压缩，保证转换/压缩有收益）。"""
    import os

    from PIL import Image
    im = Image.new("RGB", (size, size))
    im.frombytes(os.urandom(size * size * 3))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


def _make_remap_dist(tmp_path: Path, version: str = "(8, 5, 0)") -> Path:
    """最小无源码成品：game/ 里只有 script_version.txt 和一张大 PNG。"""
    dist = tmp_path / "RemapGame-pc"
    game = dist / "game"
    (game / "images").mkdir(parents=True)
    (game / "script_version.txt").write_text(version, encoding="utf-8")
    _noise_png(game / "images" / "bg.png")
    return dist


def _opts_remap() -> OptimizeOptions:
    opts = OptimizeOptions()
    opts.experimental_remap = True
    opts.use_cache = False        # 测试走真实转换路径，不命中全局缓存
    return opts


# ---------------------------------------------------------------------------
# ① remap 接线：引擎版本不支持 → 降级文案 + 图片保持原名
# ---------------------------------------------------------------------------

def test_remap_downgrade_on_old_engine(tmp_path):
    dist = _make_remap_dist(tmp_path, version="(7, 4, 0)")
    r = run_dist(str(dist), _opts_remap(),
                 str(tmp_path / "work"), str(tmp_path / "out"))
    assert any("运行时重映射暂不可用" in w and "同名压缩" in w
               for w in r["warnings"]), r["warnings"]
    wd = Path(r["working_dir"])
    assert (wd / "game" / "images" / "bg.png").exists(), "降级后图片必须保持原名"
    assert not (wd / "game" / "images" / "bg.webp").exists()
    assert not (wd / "game" / remap_mod.REMAP_SCRIPT_NAME).exists()
    assert r["remapped"] == 0


# ---------------------------------------------------------------------------
# ① remap 接线：已有注入脚本损坏 → 中止注入文案 + 图片保持原名
# ---------------------------------------------------------------------------

def test_remap_corrupted_existing_script_aborts(tmp_path):
    dist = _make_remap_dist(tmp_path)
    (dist / "game" / remap_mod.REMAP_SCRIPT_NAME).write_text(
        "_renpyslim_remap = {坏掉的json", encoding="utf-8")
    r = run_dist(str(dist), _opts_remap(),
                 str(tmp_path / "work"), str(tmp_path / "out"))
    assert any("旧映射解析失败" in w and "跳过注入" in w
               for w in r["warnings"]), r["warnings"]
    wd = Path(r["working_dir"])
    assert (wd / "game" / "images" / "bg.png").exists(), "中止注入后不许换格式"
    assert not (wd / "game" / "images" / "bg.webp").exists()
    assert r["remapped"] == 0


# ---------------------------------------------------------------------------
# ① remap 接线（对照组）：环境健康时确实转换并注入
# ---------------------------------------------------------------------------

def test_remap_happy_path_converts_and_injects(tmp_path):
    dist = _make_remap_dist(tmp_path)
    r = run_dist(str(dist), _opts_remap(),
                 str(tmp_path / "work"), str(tmp_path / "out"))
    wd = Path(r["working_dir"])
    assert not (wd / "game" / "images" / "bg.png").exists()
    assert (wd / "game" / "images" / "bg.webp").exists()
    script = wd / "game" / remap_mod.REMAP_SCRIPT_NAME
    assert script.exists(), "健康环境必须注入重映射脚本"
    mapping, ok = remap_mod.parse_remap_mapping(
        script.read_text(encoding="utf-8"))
    assert ok and mapping == {"images/bg.png": "images/bg.webp"}
    assert r["remapped"] == 1


# ---------------------------------------------------------------------------
# ① remap 接线（收口修复 1）：预检通过但注入阶段复查失败 →
# 不再静默降级，写"映射未写入请勿发布"警告并计入失败
# ---------------------------------------------------------------------------

def test_remap_injection_recheck_failure_raises_alarm(tmp_path, monkeypatch):
    dist = _make_remap_dist(tmp_path)
    game = dist / "game"
    # 上次运行留下的合法注入脚本（预检能解析 → 预检通过）
    old = remap_mod.build_remap_script({"old/x.png": "old/x.webp"})
    (game / remap_mod.REMAP_SCRIPT_NAME).write_text(old, encoding="utf-8")

    # 模拟复查阶段解析失败：第 1 次调用（预检）正常，第 2 次起失败
    real_parse = remap_mod.parse_remap_mapping
    calls = {"n": 0}

    def flaky(text):
        calls["n"] += 1
        if calls["n"] >= 2:
            return {}, False
        return real_parse(text)

    monkeypatch.setattr(remap_mod, "parse_remap_mapping", flaky)

    r = run_dist(str(dist), _opts_remap(),
                 str(tmp_path / "work"), str(tmp_path / "out"))
    wd = Path(r["working_dir"])

    # 转换确实发生了（这正是"坏包"风险场景）
    assert not (wd / "game" / "images" / "bg.png").exists()
    assert (wd / "game" / "images" / "bg.webp").exists()

    # 必须明确报警：映射未写入 + 请勿发布，绝不能再是"改走同名压缩"
    assert any("remap 映射写入失败" in w and "1 张图片映射未写入" in w
               and "请勿发布该产物" in w for w in r["warnings"]), r["warnings"]
    assert not any("改走安全的同名压缩" in w and "旧映射解析失败" in w
                   for w in r["warnings"])

    # 按既有失败上报机制计入失败项
    assert r["failed"]["image"] >= 1
    assert any("文件处理失败" in w for w in r["warnings"])

    # 旧脚本未被覆写（旧映射保全）
    assert (wd / "game" / remap_mod.REMAP_SCRIPT_NAME
            ).read_text(encoding="utf-8") == old


# ---------------------------------------------------------------------------
# ② 同名 rpa 两份：跳过重建并告警，原包不动
# ---------------------------------------------------------------------------

def test_duplicate_rpa_names_skip_rebuild_with_warning(tmp_path):
    from rtools import rpa

    dist = tmp_path / "DupRpa-pc"
    game = dist / "game"
    (game / "dup").mkdir(parents=True)
    (game / "script_version.txt").write_text("(8, 5, 0)", encoding="utf-8")

    png_buf = tmp_path / "bg.png"
    _noise_png(png_buf)
    # 尾部追加冗余字节（PNG 规范允许，PIL 照常读、重存即剥离）——
    # 保证同名压缩确定性变小、状态必为 ok，从而必然产生 rpa 替换。
    # 两份封包的内部条目故意不同名：同名封包解出目录相同（_rtools_extract/
    # <封包名>/），同名条目会落到同一物理路径，两个 job 共踩一个文件时
    # 压缩结果依赖并行时序（偶发双 skipped → 替换记录为空不进重建分支）；
    # 不同名条目各自独立物理路径，无论时序如何必然产生两条替换记录。
    data = png_buf.read_bytes() + b"\x00" * (3 * 1024 * 1024)
    w = rpa.RpaWriter(str(game / "images.rpa"))
    w.add("images/bg1.png", data)
    w.close()
    w = rpa.RpaWriter(str(game / "dup" / "images.rpa"))
    w.add("images/bg2.png", data)
    w.close()

    opts = OptimizeOptions()
    opts.use_cache = False
    r = run_dist(str(dist), opts,
                 str(tmp_path / "work"), str(tmp_path / "out"))

    assert any("2 份同名封包" in w and "跳过该批重建" in w
               for w in r["warnings"]), r["warnings"]
    wd = Path(r["working_dir"])
    rpa_files = list(wd.rglob("images.rpa"))
    assert len(rpa_files) == 2, "两份封包都必须保留"
    assert not list(wd.rglob("images.rpa.rtools.tmp")), "不许留下重建半成品"
    for f in rpa_files:
        arc = rpa.RpaArchive(str(f))
        own = "images/bg1.png" if "dup" not in f.parts else "images/bg2.png"
        assert own in arc.names(), "原封包内容必须原样保留"
        arc.close()


# ---------------------------------------------------------------------------
# ③ 超时杀进程树：抛 TimeoutExpired 且不挂起
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="杀树逻辑为 Windows 专属")
def test_run_quiet_timeout_kills_chain(tmp_path):
    from rtools.procutil import run_quiet
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        # cmd /c 链式子进程：只杀直接进程会留下孤儿跑满全程
        run_quiet(["cmd", "/c", "ping", "-n", "16", "127.0.0.1"],
                  capture_output=True, timeout=1)
    elapsed = time.monotonic() - t0
    # 杀树生效则秒级返回；留孤儿时要等 ping 跑完（约 15 秒）
    assert elapsed < 12, f"超时处理挂起了 {elapsed:.1f}s，疑似孤儿进程未杀"
    # 注：进程消失的确定性验证依赖 tasklist 轮询，跨机器不稳定，
    # 这里只保证抛错路径与不挂起（评审认可的口径）。


# ---------------------------------------------------------------------------
# ④ decompile：项目位于名为 cache 的祖先目录下不误伤脚本
# ---------------------------------------------------------------------------

def test_decompile_ancestor_dir_named_cache(tmp_path, monkeypatch):
    from rtools.decompile import _import_unrpyc, decompile_scripts

    # 祖先目录撞名 SKIP_DIRS 里的 "cache"（旧版绝对路径比对会把
    # 全部脚本静默跳过）
    game = tmp_path / "cache" / "MyGame" / "game"
    (game / "saves").mkdir(parents=True)
    (game / "script.rpyc").write_bytes(b"fake")
    (game / "saves" / "auto-1.rpyc").write_bytes(b"fake")

    unrpyc = _import_unrpyc()
    seen = []

    def spy(input_filename, context, **kwargs):
        seen.append(Path(input_filename).name)
        raise RuntimeError("spy stop")

    monkeypatch.setattr(unrpyc, "decompile_rpyc", spy)

    stats = decompile_scripts(str(game))
    assert seen == ["script.rpyc"], "祖先目录撞名不许误伤正常脚本"
    assert stats["failed"] == ["script.rpyc"]   # spy 抛错 → 常规失败路径
    assert "auto-1.rpyc" not in seen, "saves/ 目录仍须照常跳过"


# ---------------------------------------------------------------------------
# 附带：_write_json 临时文件名对齐清理契约（收口修复 3）
# ---------------------------------------------------------------------------

def test_write_json_tmp_name_matches_cleanup_contract(tmp_path):
    from rtools import cleanup
    from rtools.pipeline import _write_json

    target = tmp_path / "out" / "analysis.json"
    _write_json(target, {"ok": True})
    assert target.exists()
    assert not list(tmp_path.rglob("*.tmp")), "正常写入不许残留临时文件"

    # 硬杀场景模拟：手工造一个同命名的临时文件，清理契约必须认得
    leftover = tmp_path / "analysis.json.deadbeef.rtools.tmp"
    leftover.write_text("half", encoding="utf-8")
    assert cleanup._is_rtools_tmp(leftover.name)
    junk = cleanup.clean_junk(str(tmp_path))
    assert not leftover.exists(), "clean_junk 必须能清掉硬杀残留"
    assert any("analysis.json" in x for x in junk["removed"])
