"""阿里普惠体空壳事故的专项回归测试。

历史事故：字符集与字体对不上时，瘦身把字形删光，产出 1~2KB 空壳字体。
这里的测试模拟同样的灾难场景，验证防御机制能拦住。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rtools import font_optimizer, charset           # noqa: E402

# 用一个真实的大字体（含大量汉字）才能复现空壳场景
CJK_FONT = Path(r"E:\renpy\sdk-fonts\SourceHanSansLite.ttf")

pytestmark = pytest.mark.skipif(not CJK_FONT.exists(),
                                reason="缺少测试用的大字体")


class _KillerSubsetter:
    """模拟灾难：subset 后字形映射被清空（字符集完全对不上时的后果）。"""

    def __init__(self, options=None):
        pass

    def populate(self, text=None):
        pass

    def subset(self, font):
        font.getBestCmap().clear()


def test_empty_shell_rejected_and_original_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(font_optimizer.subset, "Subsetter", _KillerSubsetter)
    dst = tmp_path / "out.ttf"
    chars = set("你好世界中文测试字体瘦身防御机制必须拦住空壳结果")
    with pytest.raises(ValueError, match="瘦身结果异常"):
        font_optimizer.subset_font(str(CJK_FONT), str(dst), chars)
    assert not dst.exists(), "异常结果绝不能落地"
    # 原字体完好无损
    assert CJK_FONT.stat().st_size > 1024 * 1024


def test_normal_subset_not_false_positive(tmp_path):
    """正常瘦身不被误拦：保留字数与预期相符。"""
    dst = tmp_path / "out.ttf"
    chars = set("你好世界中文测试")
    res = font_optimizer.subset_font(str(CJK_FONT), str(dst), chars)
    assert res["glyphs_after"] >= 8, "该保留的字必须留下"
    assert dst.exists()


def test_gbk_file_fallback(tmp_path):
    """GBK 编码的老文件也能正确提取汉字（编码兜底）。"""
    f = tmp_path / "old.txt"
    f.write_bytes("旧代码时代的汉字文本".encode("gb18030"))
    text = charset.read_text_robust(f)
    assert "汉字" in text

    opts = charset.CharsetOptions()
    chars, warns = charset.extract_charset_sources([str(f)], opts)
    assert "汉" in chars and "字" in chars
