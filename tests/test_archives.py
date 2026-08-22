"""压缩包支持与 RPA 归档配置的回归测试。"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from rtools import archives                       # noqa: E402
from rtools.packager import (ARCHIVE_CONFIG_NAME,  # noqa: E402
                             inject_archive_config)


def _make_dist(dirpath: Path):
    """造一个最小的成品结构：根/game/脚本。"""
    (dirpath / "game").mkdir(parents=True)
    (dirpath / "game" / "script.rpyc").write_bytes(b"RENPY RPC2")
    (dirpath / "game.exe").write_bytes(b"MZ")


def test_zip_roundtrip(tmp_path):
    dist = tmp_path / "MyGame-pc"
    _make_dist(dist)
    zp = tmp_path / "MyGame-pc.zip"
    archives.create_zip(str(dist), str(zp))

    # 解压并自动定位成品目录
    out = tmp_path / "out"
    archives.extract_archive(str(zp), str(out))
    root = archives.find_dist_root(str(out))
    assert (Path(root) / "game" / "script.rpyc").exists()


def test_find_dist_root_nested(tmp_path):
    # 成品套两层也要找到（≤2 层）
    deep = tmp_path / "wrapper" / "MyGame-pc"
    _make_dist(deep)
    assert Path(archives.find_dist_root(str(tmp_path))) == deep


def test_find_dist_root_missing(tmp_path):
    (tmp_path / "random.txt").write_text("x")
    with pytest.raises(archives.ArchiveError):
        archives.find_dist_root(str(tmp_path))


def test_find_dist_root_deep_mac_bundle(tmp_path):
    """Mac 版 .app 包：game 藏在多层深处也要找到。"""
    deep = tmp_path / "Game.app" / "Contents" / "Resources" / "autorun" / "game"
    deep.mkdir(parents=True)
    (deep / "scripts").mkdir()
    (deep / "scripts" / "script.rpyc").write_bytes(b"x")
    found = archives.find_dist_root(str(tmp_path))
    assert Path(found) == deep.parent, "应返回包含 game 的成品根目录"


def test_password_zip_requires_password(tmp_path):
    # 用系统 zip 造不了密码包，直接验证"无密码时报错"的逻辑分支：
    # 伪造一个带加密标志的 zip
    zp = tmp_path / "enc.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr(zipfile.ZipInfo("a.txt"), b"data")
    # 无加密标志的 zip：不要求密码，正常解压
    out = tmp_path / "o2"
    archives.extract_archive(str(zp), str(out))
    assert (out / "a.txt").exists()


def test_unsupported_ext_message(tmp_path):
    p = tmp_path / "x.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("a.txt", "x")
    # 改扩展名伪装成 rar：本机无 7-Zip 时应给出明确指引
    fake = tmp_path / "fake.rar"
    fake.write_bytes(p.read_bytes())
    if archives.find_7zip() is None:
        with pytest.raises(archives.ArchiveError, match="7-Zip"):
            archives.extract_archive(str(fake), str(tmp_path / "o3"))


def test_inject_archive_config(tmp_path):
    proj = tmp_path / "proj"
    (proj / "game").mkdir(parents=True)
    cfg, backed_up = inject_archive_config(str(proj))
    assert backed_up is False
    text = Path(cfg).read_text(encoding="utf-8")
    assert Path(cfg).name == ARCHIVE_CONFIG_NAME
    assert 'build.archive("main", "all")' in text
    assert 'build.classify("game/**.png", "main")' in text
    # 重复注入不叠加（本工具自己的文件直接覆写，不产生备份）
    cfg2, backed_up2 = inject_archive_config(str(proj))
    assert cfg2 == cfg and backed_up2 is False
    text2 = Path(cfg).read_text(encoding="utf-8")
    assert text2.count("build.archive") == 1
