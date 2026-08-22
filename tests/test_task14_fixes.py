"""任务 14 修复项回归测试：

① build-tools 数字版本排序（9.0.0 < 35.0.0，解析失败垫底）
② 缓存体积守卫下沉（81MB 超限文件不入库，用稀疏文件构造）
③ 版本号解析剥离 - 后缀（v1.0.0-beta → (1,0,0)）
④ find_ffprobe 无 ffprobe 环境返回 None 不抛错
"""
from __future__ import annotations

from pathlib import Path

from rtools import apk, cache, scanner, updater

_MB = 1024 * 1024


# ---------- ① build-tools 版本排序 ----------

def _make_bt_dir(sdk: Path, name: str) -> Path:
    d = sdk / "rapt" / "Sdk" / "build-tools" / name
    d.mkdir(parents=True)
    (d / "zipalign.exe").write_bytes(b"FAKE-ZIPALIGN")
    (d / "apksigner.bat").write_text("@echo off", encoding="ascii")
    return d


def test_build_tools_numeric_sort(tmp_path):
    """9.0.0 与 35.0.0：数字排序必须选中 35.0.0（字符串排序会选 9）。"""
    _make_bt_dir(tmp_path, "9.0.0")
    want = _make_bt_dir(tmp_path, "35.0.0")
    za, signer = apk.find_build_tools(str(tmp_path))
    assert za == str(want / "zipalign.exe")
    assert signer == str(want / "apksigner.bat")


def test_build_tools_unparseable_last(tmp_path):
    """解析失败的目录名排最后，不让它抢过正常版本。"""
    _make_bt_dir(tmp_path, "not-a-version")
    want = _make_bt_dir(tmp_path, "2.0.0")
    za, _ = apk.find_build_tools(str(tmp_path))
    assert za == str(want / "zipalign.exe")


def test_build_tools_only_unparseable(tmp_path):
    """只有解析失败的目录时也能兜底用上（行为不回退）。"""
    want = _make_bt_dir(tmp_path, "weird")
    za, _ = apk.find_build_tools(str(tmp_path))
    assert za == str(want / "zipalign.exe")


# ---------- ② 缓存体积守卫下沉 ----------

def _sparse_file(path: Path, size: int) -> Path:
    """稀疏文件：只写首尾字节，st_size 达标但不真占磁盘。"""
    with open(path, "wb") as f:
        f.seek(size - 1)
        f.write(b"\0")
    return path


def test_store_hash_skips_oversized(tmp_path, monkeypatch):
    """store_hash 直调：81MB 超限产物不入库（守卫已下沉到裸函数）。"""
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    big = _sparse_file(tmp_path / "big.bin", (cache.MAX_CACHEABLE_MB + 1) * _MB)
    cache.store_hash("deadbeef", "act", str(big))
    assert cache.lookup_hash("deadbeef", "act") is None
    assert cache._entry_path("deadbeef", "act").exists() is False


def test_store_hash_accepts_small(tmp_path, monkeypatch):
    """小文件照常入库（守卫不误伤）。"""
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    small = tmp_path / "small.bin"
    small.write_bytes(b"hello-cache")
    cache.store_hash("cafebabe", "act", str(small))
    hit = cache.lookup_hash("cafebabe", "act")
    assert hit is not None and Path(hit).exists()


def test_store_self_skips_oversized(tmp_path, monkeypatch):
    """store_self 直调：超限产物同样不入库。"""
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    big = _sparse_file(tmp_path / "big2.bin", (cache.MAX_CACHEABLE_MB + 1) * _MB)
    cache.store_self(str(big), "act")
    h = cache.hash_file(str(big))
    assert h is not None
    assert cache.lookup_hash(h, "act") is None


def test_lookup_hash_misses_oversized_entry(tmp_path, monkeypatch):
    """lookup_hash 直调：已存在的超限条目按未命中处理。"""
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    entry = cache._entry_path("feedface", "act")
    entry.parent.mkdir(parents=True, exist_ok=True)
    _sparse_file(entry, (cache.MAX_CACHEABLE_MB + 1) * _MB)
    assert cache.lookup_hash("feedface", "act") is None


def test_store_skips_oversized_source(tmp_path, monkeypatch):
    """store() 上层入口：81MB 稀疏源文件不入库。"""
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    src = _sparse_file(tmp_path / "src.bin", (cache.MAX_CACHEABLE_MB + 1) * _MB)
    opt = tmp_path / "opt.bin"
    opt.write_bytes(b"optimized")
    cache.store(str(src), "act", str(opt))
    h = cache.hash_file(str(src))
    assert cache.lookup_hash(h, "act") is None


# ---------- ③ 版本号解析 ----------

def test_norm_strips_suffix():
    assert updater._norm("v1.0.0-beta") == (1, 0, 0)
    assert updater._norm("V2.3.4-rc.1") == (2, 3, 4)


def test_norm_plain_and_garbage():
    assert updater._norm("v0.9.0") == (0, 9, 0)
    assert updater._norm("0.11.2") == (0, 11, 2)
    assert updater._norm("garbage") == (0,)
    assert updater._norm("v1.x.0") == (0,)


# ---------- ④ find_ffprobe 双路查找 ----------

def test_find_ffprobe_missing_returns_none(monkeypatch):
    """无 ffprobe 的环境：返回 None，不抛错（不依赖真实安装）。"""
    monkeypatch.setattr(scanner, "_ffprobe_cache", None)
    monkeypatch.setattr(scanner, "_ffprobe_looked", False)
    monkeypatch.setattr(scanner.shutil, "which", lambda name: None)
    assert scanner.find_ffprobe() is None   # 不抛错，返回 None


def test_find_ffprobe_prefers_path(monkeypatch):
    """PATH 里有 ffprobe 时直接用，且结果被缓存（惰性查找）。"""
    monkeypatch.setattr(scanner, "_ffprobe_cache", None)
    monkeypatch.setattr(scanner, "_ffprobe_looked", False)
    calls = []

    def fake_which(name):
        calls.append(name)
        return "C:/fake/ffprobe.exe"

    monkeypatch.setattr(scanner.shutil, "which", fake_which)
    assert scanner.find_ffprobe() == "C:/fake/ffprobe.exe"
    assert scanner.find_ffprobe() == "C:/fake/ffprobe.exe"
    assert calls == ["ffprobe"]   # 第二次走缓存，不再 which
