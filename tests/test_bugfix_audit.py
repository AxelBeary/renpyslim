"""2026-08-15 代码审核修复的回归测试。

每条对应 STATUS.md 审核清单里核实属实并已修复的 bug，
防止将来重构时把坑再挖出来。
"""
import pickle
import zlib
import zipfile
from pathlib import Path

import pytest

from rtools import backup, cleanup, rpa, scanner
from rtools.apk import _extract_charset_from_apk
from rtools.config import BASE_CJK_PUNCT, CharsetOptions
from rtools import remap as remap_mod
from rtools.pipeline import _flush_partial_changelog
from rtools.utils import find_suffix_clashes, safe_join


# ---------------------------------------------------------------------------
# 严重 #4：保底中文标点集曾把弯引号 “”‘’ 弄丢
# ---------------------------------------------------------------------------

def test_base_cjk_punct_has_curly_quotes():
    for ch in "\u201c\u201d\u2018\u2019":
        assert ch in BASE_CJK_PUNCT
    # 也不能混入 ASCII 撇号/直引号（旧 bug 的副产物）
    assert "'" not in BASE_CJK_PUNCT


# ---------------------------------------------------------------------------
# 严重 #1：in_place 成品瘦身删存档 + 备份也漏存档
# ---------------------------------------------------------------------------

def test_backup_zip_keeps_saves(tmp_path):
    target = tmp_path / "MyGame"
    (target / "game" / "saves").mkdir(parents=True)
    (target / "game" / "saves" / "auto-1.save").write_bytes(b"save-data")
    (target / "game" / "cache").mkdir(parents=True)
    (target / "game" / "cache" / "bytecode.rpyb").write_bytes(b"x")
    out = backup.make_backup_zip(str(target), str(tmp_path / "bak.zip"))
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert any("saves/auto-1.save" in n.replace("\\", "/") for n in names)
    # 纯垃圾仍然不进备份
    assert not any("__pycache__" in n for n in names)


def test_dist_in_place_keeps_saves(tmp_path):
    """in_place 成品瘦身绝不能动 saves（曾 clean_junk 无保护）。"""
    from rtools.config import OptimizeOptions
    from rtools.pipeline import run_dist

    dist = tmp_path / "MyGame-dist"
    (dist / "game" / "saves").mkdir(parents=True)
    (dist / "game" / "saves" / "1-1.save").write_bytes(b"precious")
    (dist / "game" / "cache").mkdir(parents=True)
    (dist / "game" / "cache" / "bytecode.rpyb").write_bytes(b"x")
    (dist / "game" / "options.rpy").write_text("define x = 1", encoding="utf-8")

    opts = OptimizeOptions()
    opts.in_place = True
    result = run_dist(str(dist), opts, str(tmp_path / "work"),
                      str(tmp_path / "out"))
    assert (dist / "game" / "saves" / "1-1.save").exists()
    # in_place 下垃圾清理应被跳过并有警告说明
    assert any("跳过了垃圾清理" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# 严重 #2/#3：字符集提取曾扫不到 APK/rpa 里的脚本
# ---------------------------------------------------------------------------

def test_apk_charset_reads_plain_text(tmp_path):
    """rpy 明文以前被按 rpyc 解（zlib 失败返回空），汉字全丢。"""
    (tmp_path / "x-scripts").mkdir()
    (tmp_path / "x-scripts" / "chapter.rpy").write_text(
        'e "你好，世界"', encoding="utf-8")
    # 编译产物走 zlib 解压路径也要正常
    (tmp_path / "x-scripts" / "other.rpyc").write_bytes(
        zlib.compress(pickle.dumps("存档测试", 2)))
    chars = _extract_charset_from_apk(tmp_path, CharsetOptions())
    for ch in "你好世界存档测试":
        assert ch in chars


def test_scan_rpa_extracts_scripts_for_charset(tmp_path):
    """脚本封在 rpa 里时也得解出供字符集扫描（extract_scripts）。"""
    arc_path = tmp_path / "game" / "archive.rpa"
    arc_path.parent.mkdir(parents=True)
    w = rpa.RpaWriter(str(arc_path))
    w.add("script.rpyc", zlib.compress(pickle.dumps("封包里的汉字", 2)))
    w.add("images/pic.png", b"\x89PNG fake bytes")
    w.close()

    extract_dir = tmp_path / "extract"
    root = tmp_path / "root"
    root.mkdir()
    Path(root, "game").mkdir()
    Path(root, "game", "archive.rpa").write_bytes(arc_path.read_bytes())

    assets = scanner.scan_rpa_assets(str(root), str(extract_dir), probe=False,
                                     extract_scripts=True)
    # 脚本被解出（供字符集扫描）但不登记为资源
    assert all(a.rel != "script.rpyc" for a in assets)
    extracted = list(extract_dir.rglob("script.rpyc"))
    assert extracted, "封包内脚本没有被解出"
    # 图片照常登记
    assert any(a.rel == "images/pic.png" for a in assets)


def test_scan_rpa_zip_slip_blocked(tmp_path):
    """封包条目名含 ../ 不得逃出解包目录。"""
    root = tmp_path / "root"
    (root / "game").mkdir(parents=True)
    w = rpa.RpaWriter(str(root / "game" / "evil.rpa"))
    w.add("../evil.png", b"boom")
    w.add("ok.png", b"fine")
    w.close()

    extract_dir = tmp_path / "extract"
    assets = scanner.scan_rpa_assets(str(root), str(extract_dir), probe=False)
    assert not (tmp_path / "evil.png").exists()
    assert any(a.rel == "ok.png" for a in assets)


def test_safe_join_rejects_traversal(tmp_path):
    assert safe_join(tmp_path, "../escape.txt") is None
    assert safe_join(tmp_path, "a/../../escape.txt") is None
    assert safe_join(tmp_path, "C:/windows/evil.txt") is None
    ok = safe_join(tmp_path, "a/b.png")
    assert ok == tmp_path / "a" / "b.png"


# ---------------------------------------------------------------------------
# 中等 #1：remap 二次运行曾覆写丢旧映射
# ---------------------------------------------------------------------------

def test_remap_mapping_roundtrip():
    mapping = {"images/a.png": "images/a.webp", "images/b.jpg": "images/b.webp"}
    script = remap_mod.build_remap_script(mapping)
    parsed = remap_mod.parse_remap_mapping(script)
    assert parsed == {k.lower(): v.replace("\\", "/")
                      for k, v in mapping.items()}
    # 解析不了的内容保守返回空 dict
    assert remap_mod.parse_remap_mapping("随便一段文本") == {}


# ---------------------------------------------------------------------------
# 中等 #2：隔离区路径基准曾对不上（unused 相对 game/、按工程根拼）
# ---------------------------------------------------------------------------

def test_quarantine_moves_real_files(tmp_path):
    game = tmp_path / "game"
    (game / "audio").mkdir(parents=True)
    (game / "audio" / "unused.ogg").write_bytes(b"x")
    moved = cleanup.quarantine_files(str(game), ["audio/unused.ogg"])
    assert moved == ["audio/unused.ogg"]
    assert (game / "_rtools_quarantine" / "audio" / "unused.ogg").exists()


# ---------------------------------------------------------------------------
# 中等 #4：同名不同扩展互覆（foo.png + foo.jpg 都想变 foo.webp）
# ---------------------------------------------------------------------------

def test_find_suffix_clashes():
    rels = ["images/foo.png", "images/foo.jpg", "images/bar.png"]
    clashes = find_suffix_clashes(rels, ".webp")
    assert clashes == {"images/foo.webp"}


# ---------------------------------------------------------------------------
# 中等 #6：rpa 重建异常时 writer 句柄要关
# ---------------------------------------------------------------------------

def test_rpa_rebuild_exception_closes_writer(tmp_path):
    src = tmp_path / "src.rpa"
    w = rpa.RpaWriter(str(src))
    w.add("a.txt", b"hello")
    w.close()

    dest = tmp_path / "dest.rpa"
    with pytest.raises(OSError):
        rpa.rebuild_archive(str(src), str(dest),
                            {"a.txt": str(tmp_path / "不存在.txt")})
    # 句柄已关：dest 可以被直接删除（Windows 上句柄没关会 PermissionError）
    dest.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 中等 #7：取消后清单必须落盘（cancelled=True）
# ---------------------------------------------------------------------------

def test_flush_partial_changelog(tmp_path):
    from rtools.models import ChangeRecord
    records = [ChangeRecord(action="compress", src="a.png", detail="x")]
    _flush_partial_changelog(str(tmp_path), records, 123)
    import json
    data = json.loads((tmp_path / "changelog.json").read_text(encoding="utf-8"))
    assert data["cancelled"] is True
    assert data["saved_bytes"] == 123
    assert len(data["records"]) == 1


# ---------------------------------------------------------------------------
# 补修：lint 曾拿相对路径在 SDK 目录下找不到工程，空转还报“通过”假象
# ---------------------------------------------------------------------------

def test_lint_resolves_relative_path(tmp_path):
    from rtools import packager, verifier
    sdk = packager.find_sdk()
    if not sdk:
        pytest.skip("本机无 Ren'Py SDK")
    proj = tmp_path / "LintProj"
    (proj / "game").mkdir(parents=True)
    (proj / "game" / "script.rpy").write_text(
        'label start:\n    "hi"\n', encoding="utf-8")
    r = verifier.lint_project(sdk, str(proj))
    assert r["ran"] is True
    # 关键断言：SDK 真的找到了工程，而不是报目录不存在空转
    assert "does not exist" not in r.get("output", "")
