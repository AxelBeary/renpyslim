"""测试收集数基线：全仓唯一允许写死测试数量数字的地方。

背景：以前 README 各语言副本里散落着硬编码基线（"共 232 项"之类），
测试一变就要同步 10+ 份文档，旧数字互相漂移。现在口径改为：

- 文档一律写"数量以 `python -m pytest tests -q` 实际收集结果为准"；
- 机械核对只保留本文件一处：子进程跑一次 pytest 收集（不执行任何测试），
  把收集数与下面的集中常量做弹性区间比对（防极端漂移，不做精确相等，
  免得新增/删除用例时还要回来改这里）。

本测试只读现有 tests/ 目录，不改变任何现有测试的行为。
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# 集中常量：基线收集数（2026-09-29 `pytest --collect-only -q` 实测，含本条）。
# 只许改这一处；文档里不要再写任何测试数量数字。
TEST_COLLECT_BASELINE = 235


def test_collected_count_in_flexible_range():
    """全量收集数应落在基线的弹性区间内（0.6×～2.5×）。"""
    if getattr(sys, "frozen", False) or not (ROOT / "tests").is_dir():
        pytest.skip("打包运行或 tests/ 目录不在，跳过收集数核对")
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300,
    )
    # 尾行形如 "233 tests collected in 1.23s"（兼容单数 "1 test collected"）
    m = re.search(r"(\d+) tests? collected", out.stdout)
    if not m:
        pytest.skip(f"未能从收集输出解析测试数（stdout 尾部：{out.stdout[-200:]!r}）")
    collected = int(m.group(1))
    assert TEST_COLLECT_BASELINE * 0.6 <= collected <= TEST_COLLECT_BASELINE * 2.5, (
        f"测试收集数 {collected} 相对基线 {TEST_COLLECT_BASELINE} 漂移异常，"
        "请确认是有意增删用例后更新本文件常量"
    )
