"""tar 系压缩包支持（Issue #26）的回归测试。

覆盖：
1. is_archive 认 .tar / .tar.bz2 / .tbz2 / .tgz 等后缀
2. .tar.bz2 往返：0755 启动脚本仍可执行、普通文件、空目录都保留
3. 路径穿越成员（../evil.txt）、绝对路径成员（/etc/passwd）都被拦
4. 符号链接 / 硬链接不创建链接、不写到 dest_dir 之外
5. 设备文件 / FIFO 成员跳过且不报错
6. create_archive 对 .zip 与 .tar.bz2 都能跑通往返
全部纯 Python 构造（tarfile），不依赖外部工具。
"""
from __future__ import annotations

import io
import logging
import os
import stat
import tarfile
from pathlib import Path

import pytest

from rtools import archives


def _add_bytes(tf: tarfile.TarFile, name: str, data: bytes,
               mode: int = 0o644) -> None:
    """往 tar 里塞一个普通文件成员（可指定 mode）。"""
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    tf.addfile(info, io.BytesIO(data))


def _add_symlink(tf: tarfile.TarFile, name: str, linkname: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = linkname
    tf.addfile(info)


def _add_hardlink(tf: tarfile.TarFile, name: str, linkname: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.LNKTYPE
    info.linkname = linkname
    tf.addfile(info)


def _drop_stdlib_filter(monkeypatch) -> None:
    """模拟 Python 3.11：没有 tarfile.data_filter，只剩我们自己的校验。

    3.12+ 上标准库过滤器会先拦一遍，容易把"自己没校验"的问题掩盖掉；
    删掉属性后跑同一组用例，才能证明手写校验本身是够用的
    （3.11 上这个 delattr 是空操作）。
    """
    monkeypatch.delattr(tarfile, "data_filter", raising=False)


# ---------------------------------------------------------------------------
# 1. 后缀识别
# ---------------------------------------------------------------------------

def test_is_archive_recognizes_tar_suffixes():
    for name in ("game.tar.bz2", "game.tbz2", "game.tgz", "game.tar",
                 "game.tar.gz", "game.tar.xz"):
        assert archives.is_archive(name), f"{name} 应被识别为压缩包"
        assert archives.is_archive(name.upper()), "大写后缀（Windows 常见）也要认"


def test_is_archive_rejects_non_archive():
    for name in ("game.exe", "readme.txt", "game.tar.bz2.bak", "game.bz2"):
        assert not archives.is_archive(name), f"{name} 不该被当成压缩包"


def test_archive_suffix_takes_longest_match():
    """Path.suffix 对 foo.tar.bz2 只返回 .bz2，必须按最长后缀匹配。"""
    assert Path("foo.tar.bz2").suffix == ".bz2", "前提：标准库只给最后一段"
    assert archives._archive_suffix("foo.tar.bz2") == ".tar.bz2"
    assert archives._archive_suffix("foo.tar.gz") == ".tar.gz"
    assert archives._archive_suffix("foo.tbz2") == ".tbz2"


# ---------------------------------------------------------------------------
# 2. tar.bz2 往返：0755 启动脚本、普通文件、空目录
# ---------------------------------------------------------------------------

def test_tar_bz2_roundtrip_keeps_exec_bit_and_empty_dir(tmp_path):
    """官方 Linux 发布包的形态：renpy.sh 必须解压后仍可执行。"""
    tp = tmp_path / "Game-linux.tar.bz2"
    with tarfile.open(tp, "w:bz2") as tf:
        _add_bytes(tf, "Game-linux/game.sh", b"#!/bin/sh\necho hi\n", 0o755)
        _add_bytes(tf, "Game-linux/game/script.rpyc", b"RENPY RPC2", 0o644)
        info = tarfile.TarInfo("Game-linux/saves/")
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        tf.addfile(info)

    out = tmp_path / "out"
    archives.extract_archive(str(tp), str(out))

    script = out / "Game-linux" / "game.sh"
    assert script.read_bytes().startswith(b"#!/bin/sh"), "内容要原样落地"
    assert (out / "Game-linux" / "game" / "script.rpyc").read_bytes() == b"RENPY RPC2"
    assert (out / "Game-linux" / "saves").is_dir(), "空目录必须保留"

    if os.name == "nt":
        # Windows 的 os.chmod 只认只读位，执行位在文件系统上无从体现：
        # 只跳过这两条断言，不跳过整个用例（内容/空目录照验）；
        # "chmod 失败不许炸"由 test_tar_chmod_failure_is_silent 兜底。
        return
    assert os.access(script, os.X_OK), "0755 的脚本解压后必须仍可执行"
    assert stat.S_IMODE(script.stat().st_mode) & 0o111 == 0o111


def test_tar_chmod_failure_is_silent(tmp_path, monkeypatch):
    """os.chmod 失败必须静默忽略，不能把整次解压带崩。

    Windows 的 os.chmod 只支持只读位，对 0755 这类 mode 可能直接抛错；
    这里直接把它打成必炸，验证解压路径不因此失败（跨平台都能跑）。
    """
    def _boom(*_a, **_kw):
        raise OSError("模拟 Windows 上 chmod 不支持该 mode")

    monkeypatch.setattr(archives.os, "chmod", _boom)
    tp = tmp_path / "m.tar"
    with tarfile.open(tp, "w") as tf:
        _add_bytes(tf, "run.sh", b"#!/bin/sh\n", 0o755)

    out = tmp_path / "out"
    archives.extract_archive(str(tp), str(out))     # 不抛异常
    assert (out / "run.sh").read_bytes() == b"#!/bin/sh\n"


@pytest.mark.parametrize("suffix,mode", [(".tar", "w"), (".tgz", "w:gz"),
                                         (".tar.xz", "w:xz")])
def test_tar_variants_extract(tmp_path, suffix, mode):
    """其它 tar 后缀走的是同一条分支，各自能解出来即可。"""
    tp = tmp_path / f"G{suffix}"
    with tarfile.open(tp, mode) as tf:
        _add_bytes(tf, "G/a.txt", b"hello")
    out = tmp_path / "out"
    archives.extract_archive(str(tp), str(out))
    assert (out / "G" / "a.txt").read_bytes() == b"hello"


def test_tar_password_argument_is_ignored(tmp_path):
    """tar 不存在加密，顺手填的 zip 密码不该让 tar 解压报错。"""
    tp = tmp_path / "g.tar"
    with tarfile.open(tp, "w") as tf:
        _add_bytes(tf, "a.txt", b"x")
    out = tmp_path / "out"
    archives.extract_archive(str(tp), str(out), password="不该让这里报错")
    assert (out / "a.txt").read_bytes() == b"x"


# ---------------------------------------------------------------------------
# 3. 路径穿越 / 绝对路径：动手之前就拦，且不抛未捕获异常
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("with_stdlib_filter", [True, False])
def test_tar_traversal_member_blocked(tmp_path, monkeypatch, with_stdlib_filter):
    """成员名 ../evil.txt 绝不能写到 dest_dir 之外。

    两种环境都测：走标准库 data 过滤器（3.12+ 默认），
    以及删掉过滤器模拟 3.11、只靠手写校验。
    """
    if not with_stdlib_filter:
        _drop_stdlib_filter(monkeypatch)
    tp = tmp_path / "evil.tar"
    with tarfile.open(tp, "w") as tf:
        _add_bytes(tf, "../evil.txt", b"pwned")
        _add_bytes(tf, "ok.txt", b"fine")

    out = tmp_path / "out"
    archives.extract_archive(str(tp), str(out))     # 不抛异常

    assert not (tmp_path / "evil.txt").exists(), "穿越成员写到了目标目录之外"
    assert not (out / "evil.txt").exists()
    assert (out / "ok.txt").read_bytes() == b"fine", "正常成员不受影响"


@pytest.mark.parametrize("with_stdlib_filter", [True, False])
def test_tar_absolute_member_blocked(tmp_path, monkeypatch, with_stdlib_filter):
    """绝对路径成员（/etc/passwd 与盘符路径）一律拦下，不落到 dest_dir 里。"""
    if not with_stdlib_filter:
        _drop_stdlib_filter(monkeypatch)
    drive_target = tmp_path / "abs_evil.txt"
    tp = tmp_path / "abs.tar"
    with tarfile.open(tp, "w") as tf:
        _add_bytes(tf, "/etc/passwd", b"root:x:0:0")
        _add_bytes(tf, drive_target.as_posix(), b"pwned")
        _add_bytes(tf, "ok.txt", b"fine")

    out = tmp_path / "out"
    archives.extract_archive(str(tp), str(out))

    # 标准库过滤器会把前导 / 剥掉继续解压，我们更严：整条跳过
    assert not (out / "etc" / "passwd").exists(), "绝对路径成员不得落到解压目录"
    assert not drive_target.exists(), "盘符绝对路径成员不得写出"
    assert (out / "ok.txt").read_bytes() == b"fine"


# ---------------------------------------------------------------------------
# 4. 符号链接 / 硬链接：不建链接，也绝不跟到 dest_dir 之外
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("with_stdlib_filter", [True, False])
def test_tar_symlink_outside_not_followed(tmp_path, monkeypatch, with_stdlib_filter):
    """指向包外的符号链接：不创建、不读、不写，包外文件保持原样。"""
    if not with_stdlib_filter:
        _drop_stdlib_filter(monkeypatch)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"ORIGINAL")

    tp = tmp_path / "link.tar"
    with tarfile.open(tp, "w") as tf:
        _add_bytes(tf, "real.txt", b"inside")
        _add_symlink(tf, "evil_link", "../../outside.txt")
        _add_symlink(tf, "inner_link", "real.txt")

    out = tmp_path / "out"
    archives.extract_archive(str(tp), str(out))

    assert outside.read_bytes() == b"ORIGINAL", "包外文件不得被链接改写"
    assert not os.path.islink(out / "evil_link"), "不得创建链接成员"
    assert not (out / "evil_link").exists(), "指向包外的链接应整条跳过"
    # 包内链接按普通文件复制内容（策略见 _tar_write_link_as_file）
    inner = out / "inner_link"
    assert inner.exists() and not os.path.islink(inner), "包内链接也落地为普通文件"
    assert inner.read_bytes() == b"inside"


def test_tar_hardlink_outside_not_created(tmp_path):
    """硬链接目标在包外：跳过，不产生任何文件。"""
    tp = tmp_path / "hl.tar"
    with tarfile.open(tp, "w") as tf:
        _add_bytes(tf, "real.txt", b"inside")
        _add_hardlink(tf, "hl_outside", "../../outside.txt")
        _add_hardlink(tf, "hl_inside", "real.txt")

    out = tmp_path / "out"
    archives.extract_archive(str(tp), str(out))

    assert not (out / "hl_outside").exists(), "包外硬链接不得创建"
    assert not (tmp_path.parent / "outside.txt").exists()
    hl_in = out / "hl_inside"
    assert hl_in.exists() and hl_in.read_bytes() == b"inside", "包内硬链接复制内容"


# ---------------------------------------------------------------------------
# 5. 设备文件 / FIFO：跳过并告警，不按流打开
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("with_stdlib_filter", [True, False])
def test_tar_special_members_skipped(tmp_path, monkeypatch, caplog,
                                     with_stdlib_filter):
    if not with_stdlib_filter:
        _drop_stdlib_filter(monkeypatch)
    tp = tmp_path / "special.tar"
    with tarfile.open(tp, "w") as tf:
        _add_bytes(tf, "ok.txt", b"fine")
        fifo = tarfile.TarInfo("fifo_member")
        fifo.type = tarfile.FIFOTYPE
        tf.addfile(fifo)
        dev = tarfile.TarInfo("dev_member")
        dev.type = tarfile.CHRTYPE
        tf.addfile(dev)

    out = tmp_path / "out"
    with caplog.at_level(logging.WARNING, logger="rtools.archives"):
        archives.extract_archive(str(tp), str(out))

    assert (out / "ok.txt").read_bytes() == b"fine"
    assert not (out / "fifo_member").exists(), "FIFO 成员必须跳过"
    assert not (out / "dev_member").exists(), "设备文件成员必须跳过"
    assert any("跳过" in r.message for r in caplog.records), "应有跳过告警"


# ---------------------------------------------------------------------------
# 6. create_archive：.zip 与 .tar.bz2 都能往返
# ---------------------------------------------------------------------------

def _make_src(dirpath: Path) -> Path:
    """最小成品结构：启动脚本 + 普通文件 + 空目录。"""
    (dirpath / "game").mkdir(parents=True)
    (dirpath / "game" / "script.rpyc").write_bytes(b"RENPY RPC2")
    (dirpath / "emptydir").mkdir()
    script = dirpath / "game.sh"
    script.write_bytes(b"#!/bin/sh\nexec python game.py\n")
    if os.name != "nt":
        os.chmod(script, 0o755)        # Windows 上造不出 0755，只能跳过
    return script


def test_create_archive_zip_roundtrip(tmp_path):
    """.zip 走原 create_zip：结构往返一致。"""
    src = tmp_path / "Game-pc"
    _make_src(src)
    zp = tmp_path / "Game-pc.zip"
    assert archives.create_archive(str(src), str(zp)) == str(zp)

    out = tmp_path / "out"
    archives.extract_archive(str(zp), str(out))
    root = Path(archives.find_dist_root(str(out)))
    assert (root / "game" / "script.rpyc").exists()
    assert (root / "emptydir").is_dir()


def test_create_archive_tar_bz2_roundtrip(tmp_path):
    """.tar.bz2 走 tarfile：结构与 0755 权限都要往返得住。"""
    src = tmp_path / "Game-linux"
    script = _make_src(src)
    tp = tmp_path / "Game-linux.tar.bz2"
    assert archives.create_archive(str(src), str(tp)) == str(tp)

    # 打包结果本身：mode 落进了 tar 成员，空目录有目录条目
    with tarfile.open(tp, "r:bz2") as tf:
        members = {m.name: m for m in tf.getmembers()}
    assert "Game-linux/game/script.rpyc" in members
    assert any(m.isdir() and m.name.rstrip("/") == "Game-linux/emptydir"
               for m in members.values()), "空目录要有目录条目"
    if os.name != "nt":
        assert stat.S_IMODE(members["Game-linux/game.sh"].mode) & 0o111 == 0o111, \
            "tar 成员必须带上执行位"

    out = tmp_path / "out"
    archives.extract_archive(str(tp), str(out))
    root = Path(archives.find_dist_root(str(out)))
    assert (root / "game" / "script.rpyc").read_bytes() == b"RENPY RPC2"
    assert (root / "emptydir").is_dir(), "空目录往返后仍在"
    if os.name != "nt":
        extracted = root / "game.sh"
        assert os.access(extracted, os.X_OK), "往返后 0755 启动脚本仍可执行"
        assert stat.S_IMODE(extracted.stat().st_mode) & 0o111 == 0o111
        assert extracted.read_bytes() == script.read_bytes()


def test_create_archive_rejects_unknown_suffix(tmp_path):
    src = tmp_path / "G"
    _make_src(src)
    with pytest.raises(archives.ArchiveError, match="不支持"):
        archives.create_archive(str(src), str(tmp_path / "G.rar"))


def test_create_archive_skips_links(tmp_path, caplog):
    """沿用 create_zip 口径：符号链接跳过并告警，不跟随打包。"""
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"leak")
    src = tmp_path / "Game"
    _make_src(src)
    try:
        os.symlink(str(outside), str(src / "link.txt"))
    except OSError:
        pytest.skip("本机不支持创建符号链接")

    tp = tmp_path / "Game.tar.bz2"
    with caplog.at_level(logging.WARNING, logger="rtools.archives"):
        archives.create_archive(str(src), str(tp))
    with tarfile.open(tp, "r:bz2") as tf:
        names = tf.getnames()
    assert "Game/game.sh" in names
    assert not any("link.txt" in n for n in names), "链接不得被打包"
    assert any("跳过" in r.message for r in caplog.records), "应有跳过告警"
