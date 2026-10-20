"""磁盘空间预检回归：空间不足在复制/压缩前拦住，且给出人话下一步。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from rtools import backup


def _usage(free: int):
    return SimpleNamespace(total=free, used=0, free=free)


def test_tree_size_skips_copy_dirs(tmp_path):
    root = tmp_path / "Proj"
    (root / "game" / "cache").mkdir(parents=True)
    (root / "game" / "a.png").write_bytes(b"x" * 100)
    (root / "game" / "cache" / "big.bin").write_bytes(b"x" * 5000)
    (root / ".git").mkdir()
    (root / ".git" / "pack.idx").write_bytes(b"x" * 7000)

    assert backup._tree_size(root, backup._COPY_SKIP) == 100


def test_ensure_free_space_uses_existing_ancestor(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(backup.shutil, "disk_usage",
                        lambda p: seen.append(p) or _usage(1000))

    dest = tmp_path / "not-yet" / "deep" / "working"
    backup.ensure_free_space(dest, 800)

    assert seen == [tmp_path]


def test_margin_is_at_least_ten_percent(tmp_path, monkeypatch):
    monkeypatch.setattr(backup.shutil, "disk_usage",
                        lambda p: _usage(879))
    with pytest.raises(backup.BackupError):
        backup.ensure_free_space(tmp_path / "dest", 800)

    monkeypatch.setattr(backup.shutil, "disk_usage",
                        lambda p: _usage(880))
    backup.ensure_free_space(tmp_path / "dest", 800)


def test_shortfall_message_has_numbers_and_next_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda p: _usage(500))

    with pytest.raises(backup.BackupError) as excinfo:
        backup.ensure_free_space(tmp_path / "dest", 1000, "复制工作副本")

    message = str(excinfo.value)
    assert "复制工作副本" in message
    assert "需要" in message and "可用" in message
    assert "--work-root" in message and "--output" in message


def test_unreadable_disk_usage_does_not_block(tmp_path, monkeypatch, caplog):
    def boom(_path):
        raise OSError("no access")

    monkeypatch.setattr(backup.shutil, "disk_usage", boom)
    backup.ensure_free_space(tmp_path / "dest", 10**12)
    assert any("跳过预检" in record.message for record in caplog.records)


def test_working_copy_aborts_before_creating_dest(tmp_path, monkeypatch):
    src = tmp_path / "Game"
    (src / "game").mkdir(parents=True)
    (src / "game" / "script.rpy").write_bytes(b"x" * 1000)
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda p: _usage(1))

    dest_root = tmp_path / "work"
    with pytest.raises(backup.BackupError):
        backup.make_working_copy(str(src), str(dest_root))

    assert not dest_root.exists()
    assert (src / "game" / "script.rpy").exists()


def test_backup_zip_aborts_before_writing(tmp_path, monkeypatch):
    target = tmp_path / "Game"
    (target / "game").mkdir(parents=True)
    (target / "game" / "script.rpy").write_bytes(b"x" * 1000)
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda p: _usage(1))

    out = tmp_path / "backup.zip"
    with pytest.raises(backup.BackupError):
        backup.make_backup_zip(str(target), str(out))

    assert not out.exists()
    assert not list(tmp_path.glob("*.tmp*"))
