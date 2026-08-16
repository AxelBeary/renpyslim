"""i18n 完整性回归测试：四本语言字典键必须齐全一致，页面键必须都有翻译。

这是"不引 TypeScript 也要字典类型安全"的平价方案：
任何一本字典漏键/页面新增 data-i18n 忘了翻译，这里立刻红。
"""
import re
from pathlib import Path

HTML_PATH = Path(__file__).resolve().parent.parent / "web" / "static" / "index.html"
LANGS = ("zh", "en", "ru", "es")

_STR_CHARS = "\"'`"
_KEY_FORM = re.compile(r"[a-z_$][a-z_0-9$]*")


def _src() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def _skip_string(text: str, i: int) -> int:
    """text[i] 是引号；跳过整个字符串字面量（处理转义），返回结束引号下标。"""
    quote = text[i]
    i += 1
    n = len(text)
    while i < n:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i
        i += 1
    return i


def _lang_block(src: str, lang: str) -> str:
    """截取 I18N 里某语言字典的完整内容。

    字符串感知的花括号配对扫描：值里的 {n} 占位符不算嵌套层级。
    """
    marker = f"\n{lang}: {{"
    start = src.index(marker) + len(marker) - 1   # 指向 "{"
    depth = 0
    i = start
    n = len(src)
    while i < n:
        c = src[i]
        if c in _STR_CHARS:
            i = _skip_string(src, i)
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError(f"语言字典 {lang} 花括号不配对")


def _top_keys(block: str) -> set:
    """顶层键集合：逐字符扫描，仅取嵌套深度 1 处的 key:。

    正确处理一行多键（a: "1", b: "2"）与嵌套对象（kinds: { ... }）；
    字符串内的冒号与花括号不会误伤。
    """
    keys = set()
    depth = 0
    i = 0
    n = len(block)
    while i < n:
        c = block[i]
        if c in _STR_CHARS:
            i = _skip_string(block, i)
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == ":" and depth == 1:
            j = i - 1
            while j >= 0 and block[j] in " \t":
                j -= 1
            end = j + 1                          # 键的右边界（exclusive）
            while j >= 0 and (block[j].isalnum() or block[j] in "_$"):
                j -= 1
            key = block[j + 1:end]
            if key and _KEY_FORM.fullmatch(key):
                keys.add(key)
        i += 1
    return keys


def test_all_language_dicts_have_identical_keys():
    src = _src()
    keysets = {lang: _top_keys(_lang_block(src, lang)) for lang in LANGS}
    base = keysets["zh"]
    for lang in LANGS[1:]:
        missing = base - keysets[lang]
        extra = keysets[lang] - base
        assert not missing, f"{lang} 字典缺键：{sorted(missing)}"
        assert not extra, f"{lang} 字典多出键：{sorted(extra)}"


def test_every_page_i18n_key_is_translated():
    src = _src()
    zh_keys = _top_keys(_lang_block(src, "zh"))
    used = set(re.findall(r'data-i18n(?:-ph)?="([^"]+)"', src))
    missing = used - zh_keys
    assert not missing, f"页面用到但 zh 字典没有的键：{sorted(missing)}"


def test_langs_registry_matches_dicts():
    """LANGS 注册表必须与字典一致（语言选择器选项的事实源）。"""
    src = _src()
    m = re.search(r"const LANGS = \{([^}]+)\}", src)
    assert m, "找不到 LANGS 注册表"
    registered = set(re.findall(r'(\w+):', m.group(1)))
    assert registered == set(LANGS), \
        f"LANGS 注册表 {registered} 与测试预期 {set(LANGS)} 不一致；" \
        "新增语言请同步更新本测试的 LANGS 常量"
