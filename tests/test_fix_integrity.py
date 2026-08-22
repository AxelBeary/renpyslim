"""回归测试：zip 撞名防护、空目录保留、create_zip 防链接、RPA 短读校验。

对应修复项：
1. extract_archive 归一化撞名（大小写冲突 / 重复条目 / GBK-UTF-8 回解收敛）
2. create_zip 保留空目录
3. RpaArchive.read 截断短读报错
全部纯 Python 构造，不依赖外部工具。
"""
from __future__ import annotations

import logging
import os
import pickle
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

import pytest

from rtools.archives import create_zip, extract_archive
from rtools.rpa import RpaArchive, RpaError, RpaWriter


def test_zip_case_collision_keeps_both(tmp_path):
    """大小写撞名（Windows 大小写不敏感）：两份内容都要在，后者带 .dup。"""
    zp = tmp_path / "case.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("a/CG.txt", b"UPPER")
        zf.writestr("a/cg.txt", b"lower")
    out = tmp_path / "out"
    extract_archive(str(zp), str(out))

    files = {p.name: p.read_bytes() for p in (out / "a").iterdir()}
    assert set(files.values()) == {b"UPPER", b"lower"}, "两份内容都必须保留"
    assert len(files) == 2
    # 先到条目原名落盘，后到条目改名保留
    assert files["CG.txt"] == b"UPPER"
    dup = [name for name in files if ".dup" in name]
    assert len(dup) == 1 and files[dup[0]] == b"lower"


def test_zip_duplicate_entries_keeps_both(tmp_path):
    """完全同名条目出现两次：先到的原样，后到的带 .dup 保留。"""
    zp = tmp_path / "dup.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("a/x.txt", b"first")
        zf.writestr("a/x.txt", b"second")
    out = tmp_path / "out"
    extract_archive(str(zp), str(out))

    assert (out / "a" / "x.txt").read_bytes() == b"first"
    assert (out / "a" / "x.txt.dup1").read_bytes() == b"second"


def _write_raw_zip(path: Path, entries) -> None:
    """手写 zip 二进制（不经过 zipfile 写入器，可自由控制标志位与文件名原始字节）。

    entries: [(raw_name_bytes, data), ...]，全部存储（不压缩）、不置 UTF-8 标志。
    """
    local = bytearray()
    central = bytearray()
    for raw_name, data in entries:
        crc = zlib.crc32(data) & 0xFFFFFFFF
        offset = len(local)
        local += struct.pack("<4sHHHHHIIIHH", b"PK\x03\x04", 20, 0, 0, 0, 0,
                             crc, len(data), len(data), len(raw_name), 0)
        local += raw_name + data
        central += struct.pack("<4sHHHHHHIIIHHHHHII", b"PK\x01\x02", 20, 20,
                               0, 0, 0, 0, crc, len(data), len(data),
                               len(raw_name), 0, 0, 0, 0, 0, offset)
        central += raw_name
    eocd = struct.pack("<4sHHHHIIH", b"PK\x05\x06", 0, 0, len(entries),
                       len(entries), len(central), len(local), 0)
    path.write_bytes(bytes(local) + bytes(central) + eocd)


def test_zip_gbk_utf8_repair_convergence(tmp_path):
    """GBK/UTF-8 回解收敛为同名：一个条目名是 UTF-8 字节、一个是同一名字的
    GBK 字节（都不置 UTF-8 标志），修复后收敛为同名，后者带 .dup 保留。"""
    name = "测试.txt"
    entries = [(name.encode("utf-8"), "utf8版".encode()),
               (name.encode("gb18030"), "gbk版".encode("gb18030"))]
    zp = tmp_path / "enc.zip"
    _write_raw_zip(zp, entries)
    out = tmp_path / "out"
    extract_archive(str(zp), str(out))

    files = {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}
    expected = {"utf8版".encode(), "gbk版".encode("gb18030")}
    assert set(files.values()) == expected
    assert len(files) == 2
    dup = [n for n in files if ".dup" in n]
    assert len(dup) == 1


def test_zip_empty_dir_entry_survives_extract(tmp_path):
    """zip 内的目录条目解包后仍然存在。"""
    zp = tmp_path / "empty.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("game/empty/", b"")
        zf.writestr("game/script.rpyc", b"X")
    out = tmp_path / "out"
    extract_archive(str(zp), str(out))
    assert (out / "game" / "empty").is_dir()


def test_create_zip_keeps_empty_dir(tmp_path):
    """create_zip 打包空目录：zip 里有以 / 结尾的目录条目，解包后仍在。"""
    src = tmp_path / "MyGame-pc"
    (src / "game" / "empty").mkdir(parents=True)
    (src / "game" / "script.rpyc").write_bytes(b"X")
    zp = tmp_path / "out.zip"
    create_zip(str(src), str(zp))

    with zipfile.ZipFile(zp) as zf:
        names = zf.namelist()
    assert "MyGame-pc/game/empty/" in names

    out = tmp_path / "out"
    extract_archive(str(zp), str(out))
    assert (out / "MyGame-pc" / "game" / "empty").is_dir()


def test_create_zip_skips_links(tmp_path):
    """符号链接（文件与目录）不跟随、不打包，正常文件不受影响。"""
    src = tmp_path / "Game"
    src.mkdir()
    (src / "real.txt").write_bytes(b"keep")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"no")
    try:
        os.symlink(str(outside), str(src / "link.txt"))
    except OSError:
        pytest.skip("本机不支持创建符号链接")
    zp = tmp_path / "o.zip"
    create_zip(str(src), str(zp))
    with zipfile.ZipFile(zp) as zf:
        names = zf.namelist()
    assert "Game/real.txt" in names
    assert not any("link.txt" in n for n in names)


def test_create_zip_skips_dir_symlink(tmp_path, caplog):
    """目录符号链接不跟随：链接目标内容不进 zip，且有跳过告警记录。

    回归：旧版用 os.path.islink，Windows 上 junction 拦不住；
    新版改 os.lstat 后，真符号链接防护同样保留。
    """
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"must not leak")

    src = tmp_path / "Game"
    src.mkdir()
    (src / "real.txt").write_bytes(b"keep")
    try:
        os.symlink(str(outside), str(src / "link_dir"),
                   target_is_directory=True)
    except OSError:
        pytest.skip("本机不支持创建目录符号链接")
    zp = tmp_path / "o.zip"
    with caplog.at_level(logging.WARNING, logger="rtools.archives"):
        create_zip(str(src), str(zp))
    with zipfile.ZipFile(zp) as zf:
        names = zf.namelist()
    assert "Game/real.txt" in names
    assert not any("link_dir" in n for n in names), "链接目录不得被打包"
    assert not any("secret.txt" in n for n in names), "链接目标内容不得泄漏"
    assert any("跳过" in r.message for r in caplog.records), "应有跳过告警记录"


def test_create_zip_skips_junction(tmp_path, caplog):
    """Windows junction 真被跳过：内容不进 zip 且有告警记录。

    回归：评审实测 os.path.islink 对 junction 返回 False、
    is_dir(follow_symlinks=False) 返回 True，旧防护形同虚设，
    junction 被递归穿入（成环时栈溢出）。非 Windows / 无权限则 skip。
    """
    if sys.platform != "win32":
        pytest.skip("junction 是 Windows 专属概念")
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"must not leak")

    src = tmp_path / "Game"
    src.mkdir()
    (src / "real.txt").write_bytes(b"keep")
    link = src / "junc"
    proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                          capture_output=True, timeout=30, check=False)
    if proc.returncode != 0 or not link.exists():
        pytest.skip("本机无法创建 junction（权限或环境限制）")
    try:
        zp = tmp_path / "o.zip"
        with caplog.at_level(logging.WARNING, logger="rtools.archives"):
            create_zip(str(src), str(zp))
        with zipfile.ZipFile(zp) as zf:
            names = zf.namelist()
        assert "Game/real.txt" in names
        assert not any("junc" in n for n in names), "junction 不得被打包"
        assert not any("secret.txt" in n for n in names), "junction 目标内容不得泄漏"
        assert any("跳过" in r.message for r in caplog.records), "应有跳过告警记录"
    finally:
        subprocess.run(["cmd", "/c", "rmdir", str(link)],
                       capture_output=True, timeout=30, check=False)


def _write_truncated_rpa(path: Path) -> None:
    """构造一个索引声称长度远大于实际数据的封包（模拟文件被截断）。"""
    key = 0x42424242
    data = b"hello rpa"
    body = b"Made with Ren'Py." + data
    # 声称长度 5000，实际文件里该偏移后只剩索引字节 → 短读
    entries = {"a.txt": [(len(body) ^ key, 5000 ^ key, b"")]}
    blob = zlib.compress(pickle.dumps(entries, 2), 3)
    header_len = 34  # "RPA-3.0 " + 16位偏移 + 空格 + 8位密钥 + 换行，定长
    header = b"RPA-3.0 %016x %08x\n" % (header_len + len(body), key)
    path.write_bytes(header + body + blob)


def test_rpa_truncated_read_raises(tmp_path):
    """截断的封包调 read() 必须抛 RpaError，且信息含条目名与长度。"""
    p = tmp_path / "t.rpa"
    _write_truncated_rpa(p)
    arc = RpaArchive(str(p))
    try:
        with pytest.raises(RpaError, match="a.txt"):
            arc.read("a.txt")
    finally:
        arc.close()


def test_rpa_intact_read_still_works(tmp_path):
    """未截断封包的正常读取路径不受短读校验影响。"""
    p = tmp_path / "ok.rpa"
    w = RpaWriter(str(p))
    w.add("game/script.rpyc", b"RENPY RPC2 demo data")
    w.close()
    arc = RpaArchive(str(p))
    try:
        assert arc.read("game/script.rpyc") == b"RENPY RPC2 demo data"
    finally:
        arc.close()
