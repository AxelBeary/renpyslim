"""反编译缺陷修复的回归测试。

覆盖三项修复：
1. 写盘中途失败时，半成品 .rpy 必须被删除、状态记为 failed，
   且重跑时会自动重试（而不是因文件已存在被永久跳过）。
2. 大写扩展名（.RPYC/.RPYMC）不再让 vendored unrpyc 抛
   UnboundLocalError，而是被正常归类处理后走常规失败路径。
3. SKIP_DIRS（saves/cache 等）目录下的伪 rpyc 不被处理。

样本构造参考 tests/test_audit_20260817.py::test_decompile_roundtrip，
但不依赖本机 SDK：通过 monkeypatch vendored unrpyc 的内部函数
模拟各阶段行为，测试可在任意环境稳定运行。
"""
from __future__ import annotations

from pathlib import Path

from rtools.decompile import _import_unrpyc, decompile_scripts


def _make_rpyc(path: Path, data: bytes = b"not a real rpyc payload") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_failed_decompile_cleans_partial_output(tmp_path, monkeypatch):
    """写盘阶段抛异常：半成品被删、计为 failed、重跑自动重试。"""
    unrpyc = _import_unrpyc()
    game = tmp_path / "game"
    _make_rpyc(game / "script.rpyc")
    _make_rpyc(game / "module.rpymc")

    attempts = []

    def fake_get_ast(in_file, try_harder, context):
        # 跳过真实解析，让流程走到"打开输出文件写盘"这一步
        return []

    def boom_pprint(out_file, ast, options):
        # 模拟真实故障：文件已打开并写入了一部分，然后崩溃
        out_file.write("# half-written garbage\n")
        attempts.append(1)
        raise RuntimeError("simulated crash during write")

    monkeypatch.setattr(unrpyc, "get_ast", fake_get_ast)
    monkeypatch.setattr(unrpyc.decompiler, "pprint", boom_pprint)

    stats = decompile_scripts(str(game))
    assert stats["decompiled"] == 0
    assert sorted(stats["failed"]) == ["module.rpymc", "script.rpyc"]
    assert len(attempts) == 2

    # 半成品不得残留：目录里没有任何 .rpy/.rpym
    leftovers = [p for p in game.rglob("*")
                 if p.suffix.lower() in (".rpy", ".rpym")]
    assert leftovers == []

    # 重跑必须重试（输出文件不存在，不会被 skip 分支短路）
    stats2 = decompile_scripts(str(game))
    assert len(attempts) == 4          # 两个文件各又尝试了一次
    assert stats2["skipped"] == 0
    assert sorted(stats2["failed"]) == ["module.rpymc", "script.rpyc"]
    leftovers = [p for p in game.rglob("*")
                 if p.suffix.lower() in (".rpy", ".rpym")]
    assert leftovers == []


def test_uppercase_suffix_no_unboundlocal_error(tmp_path):
    """.RPYC 大写扩展名：不再抛 UnboundLocalError，正常归类后走失败路径。"""
    unrpyc = _import_unrpyc()
    f = _make_rpyc(tmp_path / "game" / "SCRIPT.RPYC")
    ctx = unrpyc.Context()
    # 旧代码在这里因三分支全不中而抛 UnboundLocalError；
    # 修复后后缀归类正常通过，随后因内容是垃圾数据在
    # 解析阶段抛 BadRpycException（常规失败路径）
    exc = None
    try:
        unrpyc.decompile_rpyc(f, ctx)
    except Exception as e:  # noqa: BLE001 — 测试就是要捕获任意异常做断言
        exc = e
    assert not isinstance(exc, UnboundLocalError), "大写扩展名仍触发 UnboundLocalError"
    assert ctx.state != "ok"

    # rpymc 同理
    fm = _make_rpyc(tmp_path / "game" / "MODULE.RPYMC")
    ctx2 = unrpyc.Context()
    exc2 = None
    try:
        unrpyc.decompile_rpyc(fm, ctx2)
    except Exception as e:  # noqa: BLE001 — 测试就是要捕获任意异常做断言
        exc2 = e
    assert not isinstance(exc2, UnboundLocalError), "大写扩展名仍触发 UnboundLocalError"
    assert ctx2.state != "ok"


def test_uppercase_suffix_via_pipeline(tmp_path):
    """大写扩展名经 decompile_scripts 全流程：记为 failed，不崩溃、无残留。"""
    game = tmp_path / "game"
    _make_rpyc(game / "SCRIPT.RPYC")
    stats = decompile_scripts(str(game))
    assert stats["decompiled"] == 0
    assert stats["failed"] == ["SCRIPT.RPYC"]
    assert not list(game.rglob("*.rpy"))


def test_skip_dirs_and_stale_partials_not_processed(tmp_path, monkeypatch):
    """saves/cache 下的伪 rpyc 不被处理；上一轮残留的半成品源也不被处理。"""
    unrpyc = _import_unrpyc()
    game = tmp_path / "game"
    _make_rpyc(game / "script.rpyc")                    # 唯一合法目标
    _make_rpyc(game / "saves" / "auto-1.rpyc")          # SKIP_DIRS
    _make_rpyc(game / "cache" / "bytes.rpyc")           # SKIP_DIRS
    _make_rpyc(game / "leftover.rpy.rpyc")              # 上轮残留半成品

    seen = []

    def spy_decompile(input_filename, context, **kwargs):
        seen.append(Path(input_filename).name)
        raise RuntimeError("stop after recording")

    monkeypatch.setattr(unrpyc, "decompile_rpyc", spy_decompile)

    stats = decompile_scripts(str(game))
    assert seen == ["script.rpyc"]
    assert stats["failed"] == ["script.rpyc"]
    # saves/cache 下没有生成任何输出
    assert not (game / "saves" / "auto-1.rpy").exists()
    assert not (game / "cache" / "bytes.rpy").exists()
    # 半成品源没有产生二次产物
    assert not (game / "leftover.rpy.rpy").exists()
