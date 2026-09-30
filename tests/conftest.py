"""pytest 统一配置：把项目根目录加入 sys.path，并提供自给字体夹具。

所有测试直接 `from rtools import ...` 即可，无需各自插 sys.path。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _build_minimal_ttf(path, chars):
    """用 fontTools 程序化生成含指定码位的最小 TrueType 字体。

    每个字形画一个多边形轮廓（尺寸随码位变化，保证子集化后体积确实
    变小），不依赖任何本机外部字体文件，CI 上也能真实执行测试。
    """
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    names = ["uni%04X" % ord(c) for c in chars]
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef"] + names)
    fb.setupCharacterMap({ord(c): n for c, n in zip(chars, names)})

    def _outline(i):
        pen = TTGlyphPen(None)
        r = 350 + (i % 30) * 5
        cx, cy = 500, 500
        pts = [(cx, cy + r), (cx + r, cy), (cx, cy - r), (cx - r, cy),
               (cx - r // 2, cy - r // 2), (cx + r // 2, cy - r // 2)]
        pen.moveTo(pts[0])
        for pt in pts[1:]:
            pen.lineTo(pt)
        pen.closePath()
        return pen.glyph()

    glyphs = {".notdef": TTGlyphPen(None).glyph()}
    for i, n in enumerate(names):
        glyphs[n] = _outline(i)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({n: (1000, 0) for n in [".notdef"] + names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "RToolsTestCJK",
                       "styleName": "Regular"})
    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200,
                usWinAscent=800, usWinDescent=200)
    fb.setupPost()
    fb.save(str(path))


@pytest.fixture(scope="session")
def cjk_font(tmp_path_factory):
    """自给的大字符集 CJK 测试字体（ASCII + 数千汉字字形）。

    历史教训：早先夹具直接引用本机外部字体绝对路径，CI 机器上不存在，
    守空壳事故的回归测试天天静默跳过。改为运行时程序化生成，
    任何环境（含 CI）都真实执行，不再依赖本机路径。
    """
    chars = [chr(c) for c in range(0x20, 0x7F)]               # ASCII
    chars += [chr(c) for c in range(0x4E00, 0x9FA6)]          # CJK 统一表意文字全段
    font = tmp_path_factory.mktemp("cjkfont") / "test_cjk.ttf"
    _build_minimal_ttf(font, sorted(set(chars)))
    return font


# 外部依赖跳过白名单：reason 子串命中即视为已登记的合法跳过。
# 只收真实外部工具（FFmpeg/Ren'Py SDK/keytool/build-tools）与平台性跳过。
_EMBEDDED_SKIP_REASONS = {
    "缺少测试客户端依赖": None,      # httpx 未安装（TestClient 不可用）
    "Windows 专属": None,            # 平台性 skipif（非外部依赖）
    "junction 是 Windows 专属概念": None,
    "本机不支持创建符号链接": 1,
    "本机不支持创建目录符号链接": 1,
    "本机无法创建 junction": 1,
    "本机没有 ffmpeg": None,
    "本机无 ffmpeg": None,
    "本机无 Ren'Py SDK": None,
    "本机无 Android build-tools": None,
    "build-tools": None,
    "本机无 keytool": None,
}
_skipped_reasons: list[str] = []


def pytest_runtest_logreport(report):
    """收集运行期实际发生的 skip，供收口钩子对白名单核账。"""
    if report.skipped:
        _skipped_reasons.append(str(report.longrepr[-1])
                                if report.longrepr else "")


@pytest.hookimpl(trylast=True)
def pytest_terminal_summary(terminalreporter):
    """跳过守卫（防静默全绿）：未登记的 skip 一律判 CI 失败。

    历史教训：守空壳事故的回归测试曾因夹具引用本机外部字体绝对路径，
    在 CI 天天静默跳过而无人察觉。此后新增本机路径类 skipif 必须
    先在本白名单登记，否则测试会话直接红。白名单只应收真实外部
    依赖，能自给的夹具（如 cjk_font）禁止加入。
    """
    unregistered = [r for r in _skipped_reasons
                    if not any(k in r for k in _EMBEDDED_SKIP_REASONS)]
    if unregistered:
        terminalreporter.write_sep("!", "未登记的外部依赖跳过（CI 跳过守卫）")
        for r in unregistered:
            terminalreporter.write_line(f"  skip: {r}")
        terminalreporter.write_line(
            "  处理：改造成自给夹具，或确认确属外部依赖后登记进 "
            "tests/conftest.py 的 _EMBEDDED_SKIP_REASONS")
        # 抛 pytest.exit：异常退出码判 1，CI 步骤显式失败而非静默全绿
        raise pytest.exit("CI 跳过守卫：存在未登记的外部依赖跳过",
                          returncode=1)

