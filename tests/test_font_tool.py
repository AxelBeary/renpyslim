"""独立字体瘦身（font_tool）的回归测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from rtools import font_tool                       # noqa: E402
from test_core import _make_tiny_font              # noqa: E402


def test_slim_single_font(tmp_path):
    font = tmp_path / "t.ttf"
    _make_tiny_font(font)                     # 只有 A、B 字形
    text = tmp_path / "script.txt"
    text.write_text("AAA BBB 你好", encoding="utf-8")

    res = font_tool.run_font_slim(str(font), [str(text)],
                                  output_dir=str(tmp_path / "out"))
    assert res["charset_size"] > 0
    assert len(res["outputs"]) == 1
    out = Path(res["outputs"][0]["out"])
    assert out.exists() and out.name == "t-slim.ttf"
    assert font.exists(), "原件绝不能被动"
    assert Path(res["charlist"]).exists()
    # 字符清单内容应包含保底拉丁字母
    content = Path(res["charlist"]).read_text(encoding="utf-8")
    assert "A" in content and "a" in content


def test_slim_never_overwrites(tmp_path):
    font = tmp_path / "t.ttf"
    _make_tiny_font(font)
    text = tmp_path / "s.txt"
    text.write_text("AB", encoding="utf-8")
    # 先占住输出名
    (tmp_path / "t-slim.ttf").write_bytes(b"occupied")

    res = font_tool.run_font_slim(str(font), [str(text)])
    out = res["outputs"][0]["out"]
    assert Path(out).name == "t-slim-2.ttf", "已存在时必须加序号，不能覆盖"
    assert (tmp_path / "t-slim.ttf").read_bytes() == b"occupied"


def test_slim_rejects_bad_inputs(tmp_path):
    font = tmp_path / "t.ttf"
    _make_tiny_font(font)
    with pytest.raises(font_tool.FontSlimError):
        font_tool.run_font_slim(str(font), [])          # 没有文本来源
    with pytest.raises(font_tool.FontSlimError):
        font_tool.run_font_slim(str(tmp_path / "x.txt"), ["a"])  # 不是字体
    with pytest.raises(font_tool.FontSlimError):
        font_tool.run_font_slim(str(tmp_path / "nope.ttf"), [str(font)])


def test_charlist_format(tmp_path):
    chars = set("你好世界ABC")
    dest = font_tool.write_charlist(chars, str(tmp_path / "list.txt"))
    content = Path(dest).read_text(encoding="utf-8")
    assert "共 7 个字符" in content
    for c in "你好世界ABC":
        assert c in content
