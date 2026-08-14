"""静默执行与扫描进度的回归测试。"""
from __future__ import annotations

import sys

from rtools.procutil import run_quiet          # noqa: E402
from rtools import scanner                     # noqa: E402
from rtools.models import AssetKind            # noqa: E402


def test_run_quiet_works():
    """静默封装不改变正常行为，且 Windows 下不弹窗口。"""
    r = run_quiet([sys.executable, "-c", "print('hi')"], capture_output=True)
    assert r.returncode == 0
    assert b"hi" in r.stdout


def test_scan_assets_progress(tmp_path):
    # 造 15 个假图片，验证进度回调带着总数和序号
    for i in range(15):
        (tmp_path / f"img{i:02d}.png").write_bytes(b"\x89PNG fake")
    seen = []
    assets = scanner.scan_assets(str(tmp_path), probe=False,
                                 progress=lambda i, t, n: seen.append((i, t, n)))
    assert len(assets) == 15
    assert all(a.kind == AssetKind.IMAGE for a in assets)
    assert seen, "进度回调没有被触发"
    totals = {t for _, t, _ in seen}
    assert totals == {15}, f"进度总数应为 15，实际 {totals}"
    assert seen[-1][0] == 15, "最后一条进度应报到最后一个文件"
