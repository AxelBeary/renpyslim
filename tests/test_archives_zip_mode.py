"""zip 往返保留 Unix 执行位（Issue #41）的回归测试。

背景：tar 系（Issue #26）早就把执行位往返做对了，zip 一直没有——
解压侧读不到 ZipInfo.external_attr，回包侧也不写权限位，于是 Linux
发布包里 0755 的 renpy.sh 走一趟 zip 就变成不可执行。

口径（与 tar 系逐字一致，共用 _set_unix_mode / _member_write_mode）：

1. 解压：mode = (external_attr >> 16) & 0o777，只认 create_system=3
   （Unix）的成员，剥掉 setuid/setgid/sticky，只留 0o755 集合内的位；
2. 回包：每个普通文件成员写 external_attr = mode << 16 + create_system=3；
3. **Windows 行为零变化**：既不还原也不指望执行位，回包固定 0644。

执行位在 NTFS 上无从体现，相关断言按 os.name 分支跳过
（POSIX-only 部分用 reason="POSIX 专属：…" 登记，见 tests/conftest.py）。
"""
from __future__ import annotations

import os
import stat
import zipfile
from pathlib import Path

import pytest

from rtools import archives


def _make_zip(path: Path, members: dict[str, tuple[bytes, int]]) -> None:
    """按 {成员名: (内容, mode)} 造一个 Unix 写出来的 zip。

    create_system 置 3（Unix）才带得动权限位——这正是 Linux 上
    zip / tar 打包工具的实际行为，也是解压侧唯一信任的形态。
    """
    with zipfile.ZipFile(path, "w") as zf:
        for name, (data, mode) in members.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)


# ---------------------------------------------------------------------------
# 1. 解压：从 external_attr 还原（POSIX）
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="POSIX 专属：执行位语义")
def test_zip_extract_keeps_exec_bit(tmp_path):
    """0755 的启动脚本解压后必须仍可执行（Issue #41 的核心诉求）。"""
    zp = tmp_path / "Game.zip"
    _make_zip(zp, {
        "Game/renpy.sh": (b"#!/bin/sh\necho hi\n", 0o755),
        "Game/game/script.rpyc": (b"RENPY RPC2", 0o644),
    })

    out = tmp_path / "out"
    archives.extract_archive(str(zp), str(out))

    script = out / "Game" / "renpy.sh"
    assert script.read_bytes().startswith(b"#!/bin/sh"), "内容要原样落地"
    assert os.access(script, os.X_OK), "0755 的脚本解压后必须仍可执行"
    assert stat.S_IMODE(script.stat().st_mode) & 0o111 == 0o111


@pytest.mark.skipif(os.name == "nt", reason="POSIX 专属：执行位语义")
def test_zip_extract_keeps_0644_without_exec_bits(tmp_path):
    """0644 往返仍是 0644：不能顺手给所有文件加执行位。"""
    zp = tmp_path / "Game.zip"
    _make_zip(zp, {"Game/game/data.rpyc": (b"data", 0o644)})

    out = tmp_path / "out"
    archives.extract_archive(str(zp), str(out))

    mode = stat.S_IMODE((out / "Game" / "game" / "data.rpyc").stat().st_mode)
    assert mode == 0o644, f"0644 应原样往返，实际 {mode:o}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX 专属：setuid 语义")
def test_zip_extract_strips_setuid_setgid_sticky(tmp_path):
    """带 setuid/setgid/sticky 的成员必须被剥掉，与 tar 系口径一致。"""
    zp = tmp_path / "evil.zip"
    _make_zip(zp, {"Game/run.sh": (b"#!/bin/sh\necho hi\n", 0o4755)})

    out = tmp_path / "out"
    archives.extract_archive(str(zp), str(out))

    st = (out / "Game" / "run.sh").stat()
    mode = stat.S_IMODE(st.st_mode)
    assert mode & 0o7000 == 0, f"setuid/setgid/sticky 必须剥掉，实际 {mode:o}"
    assert mode & 0o111, "普通执行位要保留（剥的是特殊位，不是执行位）"


@pytest.mark.skipif(os.name == "nt", reason="POSIX 专属：执行位语义")
def test_zip_extract_ignores_windows_written_entries(tmp_path):
    """create_system=0（Windows 打的包）不按高位猜权限，保持系统默认。

    资源管理器 / 7-Zip 默认 FAT 口径，external_attr 高 16 位是空的或
    DOS 属性，照搬会让解出来的文件权限莫名其妙，所以解压侧只信
    create_system=3。这里断言"没被硬塞 0755"。
    """
    zp = tmp_path / "win.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        info = zipfile.ZipInfo("Game/tool.exe")
        info.create_system = 0            # FAT / Windows
        info.external_attr = 0x20         # 只有 DOS 归档属性位
        zf.writestr(info, b"MZ")

    out = tmp_path / "out"
    archives.extract_archive(str(zp), str(out))

    mode = stat.S_IMODE((out / "Game" / "tool.exe").stat().st_mode)
    assert not mode & 0o111, f"Windows 写的条目不该凭空多出执行位（{mode:o}）"


# ---------------------------------------------------------------------------
# 2. 回包：写 external_attr（POSIX 断言执行位，Windows 断言固定 0644）
# ---------------------------------------------------------------------------

def test_zip_repack_roundtrip_keeps_mode(tmp_path):
    """源文件 0755 → 打包 → 解压 仍是 0755（POSIX）；Windows 断言 0644。"""
    src = tmp_path / "Game"
    (src / "game").mkdir(parents=True)
    script = src / "renpy.sh"
    script.write_bytes(b"#!/bin/sh\necho hi\n")
    data = src / "game" / "data.rpyc"
    data.write_bytes(b"data")
    if os.name == "posix":
        script.chmod(0o755)
        data.chmod(0o644)

    zp = tmp_path / "Game.zip"
    archives.create_zip(str(src), str(zp))

    with zipfile.ZipFile(zp) as zf:
        infos = {i.filename: i for i in zf.infolist()}
        # 两个平台都成立的契约：Unix 口径 + 权限位落在高 16 位
        assert infos["Game/renpy.sh"].create_system == 3
        assert infos["Game/renpy.sh"].external_attr >> 16 == (
            0o755 if os.name == "posix" else 0o644)

    if os.name != "posix":
        # Windows 的 stat 没有执行位可言，回包固定 0644（与改动前一致）
        return

    out = tmp_path / "out"
    archives.extract_archive(str(zp), str(out))
    assert os.access(out / "Game" / "renpy.sh", os.X_OK), "往返后仍应可执行"
    assert stat.S_IMODE((out / "Game" / "renpy.sh").stat().st_mode) == 0o755
    assert stat.S_IMODE((out / "Game" / "game" / "data.rpyc").stat().st_mode) == 0o644


def test_zip_roundtrip_keeps_0644(tmp_path):
    """0644 成员往返仍是 0644（两个平台都跑，POSIX 断言权限位）。"""
    zp = tmp_path / "Game.zip"
    _make_zip(zp, {"Game/renpy.sh": (b"#!/bin/sh\n", 0o644)})

    out = tmp_path / "out"
    archives.extract_archive(str(zp), str(out))
    assert (out / "Game" / "renpy.sh").read_bytes() == b"#!/bin/sh\n"

    if os.name != "posix":
        return              # Windows 上执行位/权限位无从体现，只验内容
    mode = stat.S_IMODE((out / "Game" / "renpy.sh").stat().st_mode)
    assert mode == 0o644 and not mode & 0o111


def test_zip_chmod_failure_is_silent(tmp_path, monkeypatch):
    """os.chmod 失败必须静默忽略，不能把整次解压带崩。

    与 test_archives_tar.py 的同名用例对应：zip 分支现在也会 chmod，
    失败路径同样不许抛。
    """
    def _boom(*_a, **_kw):
        raise OSError("模拟 chmod 不支持该 mode")

    zp = tmp_path / "Game.zip"
    _make_zip(zp, {"Game/run.sh": (b"#!/bin/sh\n", 0o755)})

    monkeypatch.setattr(archives.os, "chmod", _boom)
    out = tmp_path / "out"
    archives.extract_archive(str(zp), str(out))     # 不抛异常
    assert (out / "Game" / "run.sh").read_bytes() == b"#!/bin/sh\n"
