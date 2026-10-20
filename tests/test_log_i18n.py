"""八语结构化日志翻译层（WP6b）契约测试。

WP6a 让后端 p.emit 带上了 code/params；WP6b 在 i18n.js 里按 code
提供八语模板。这里静态核对三件事：

- 后端三份源码发出的每个 code 都有 LOG_CODES 条目，且恰有 8 种语言；
- 模板占位符只使用该 code 后端 params 真实存在的键，各语言占位符一致；
- app.js 把整条日志对象交给 trLog（不是只传 message），旧正则回退仍在。

Node 可用时再跑一遍真实函数（缺 Node 自动 skip，与 test_frontend_syntax 同口径）。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "web" / "static"
RT = ROOT / "rtools"
MODULES = ("pipeline.py", "apk.py", "font_tool.py")
LANGS = ("zh", "en", "ru", "es", "pt", "tr", "de", "fr")

_CALL = re.compile(r"p\.emit\(")
_CODE = re.compile(r'code="([^"]+)"')
_PARAMS = re.compile(r"params\s*=\s*\{(.*?)\}", re.S)
_PARAM_KEY = re.compile(r'"([A-Za-z_]\w*)"\s*:')
_PLACEHOLDER = re.compile(r"\{([A-Za-z_]\w*)\}")


def _backend_codes() -> dict[str, set[str]]:
    """扫描三份后端源码：code -> 该 code 所有 emit 调用里出现过的 params 键。"""
    found: dict[str, set[str]] = {}
    for name in MODULES:
        text = (RT / name).read_text(encoding="utf-8")
        starts = [m.start() for m in _CALL.finditer(text)]
        for idx, start in enumerate(starts):
            end = starts[idx + 1] if idx + 1 < len(starts) else len(text)
            chunk = text[start:end]
            m = _CODE.search(chunk)
            if not m:
                continue
            keys = found.setdefault(m.group(1), set())
            params = _PARAMS.search(chunk)
            if params:
                keys.update(_PARAM_KEY.findall(params.group(1)))
    return found


def _js_object_block(src: str, marker: str) -> str:
    """截取 marker 后的第一个 {...} 字面量。

    字符串感知：模板里的 {i} 只是普通字符，不参与花括号配对。
    """
    start = src.index(marker) + len(marker)
    while start < len(src) and src[start] in " \t\r\n":
        start += 1
    assert src[start] == "{", f"{marker} 后不是对象字面量"
    depth = 0
    i = start
    quote = ""
    while i < len(src):
        c = src[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c in "\"'`":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError(f"{marker} 对象花括号不配对")


def _log_codes() -> dict:
    src = (STATIC / "i18n.js").read_text(encoding="utf-8")
    return json.loads(_js_object_block(src, "const LOG_CODES = "))


def test_every_backend_code_has_a_log_entry():
    backend = _backend_codes()
    codes = _log_codes()
    missing = sorted(set(backend) - set(codes))
    assert not missing, f"后端已发出但 LOG_CODES 没有的 code：{missing}"
    stale = sorted(set(codes) - set(backend))
    assert not stale, f"LOG_CODES 有但后端已不再发出的 code：{stale}"


def test_each_code_has_exactly_eight_languages():
    for code, templates in _log_codes().items():
        assert isinstance(templates, dict), f"{code} 的模板不是对象"
        assert list(templates) == list(LANGS), \
            f"{code} 语言键应为 {list(LANGS)}，实际 {list(templates)}"
        for lang in LANGS:
            tpl = templates[lang]
            assert isinstance(tpl, str) and tpl.strip(), \
                f"{code}.{lang} 模板为空或不是字符串"


def test_placeholders_are_backend_params_and_match_across_languages():
    backend = _backend_codes()
    for code, templates in _log_codes().items():
        per_lang = {lang: set(_PLACEHOLDER.findall(tpl))
                    for lang, tpl in templates.items()}
        first = per_lang[LANGS[0]]
        for lang in LANGS[1:]:
            assert per_lang[lang] == first, \
                f"{code} 各语言占位符不一致：zh={sorted(first)}，" \
                f"{lang}={sorted(per_lang[lang])}"
        unknown = first - backend.get(code, set())
        assert not unknown, \
            f"{code} 模板占位符 {sorted(unknown)} 在后端 params 里不存在" \
            f"（该 code 的 params：{sorted(backend.get(code, set()))}）"


def test_app_js_passes_whole_log_object_and_keeps_pattern_fallback():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "trLog(l.message)" not in app, \
        "轮询处仍在只传 message，结构化 code/params 会被丢掉"
    assert app.count("trLog(l)") >= 6, "trLog(l) 调用点数量异常"

    i18n = (STATIC / "i18n.js").read_text(encoding="utf-8")
    assert "const LOG_PATTERNS =" in i18n
    assert "for (const [re, tpl] of LOG_PATTERNS)" in i18n, \
        "旧中文句式正则回退被删了"
    assert "function fillLogTemplate" in i18n


def test_trlog_runtime_behaviour_with_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不在 PATH，跳过 trLog 运行时测试")

    js = (STATIC / "i18n.js").read_text(encoding="utf-8")
    harness = r"""
function expect(actual, expected, label) {
  if (actual !== expected) {
    console.error(label + " → " + JSON.stringify(actual));
    process.exit(1);
  }
}
LANG = "en";
expect(trLog({message: "ignored", code: "scan_progress",
              params: {i: 1, total: 2, name: "a"}}),
       "Scanning assets 1/2: a", "英文结构化翻译");
LANG = "fr";
expect(trLog({message: "ignored", code: "scan_progress",
              params: {i: 1, total: 2, name: "a"}}),
       "Scan des assets 1/2: a", "法文结构化翻译");
LANG = "zh";
expect(trLog({message: "ignored", code: "scan_progress",
              params: {i: 1, total: 2, name: "a"}}),
       "扫描资源 1/2：a", "中文结构化翻译");
LANG = "en";
var missing = trLog({message: "ignored", code: "scan_progress"});
if (/\{[A-Za-z_]/.test(missing)) {
  console.error("缺 params 时泄漏占位符：" + missing);
  process.exit(1);
}
expect(trLog({message: "ignored", code: "scan_progress",
              params: {i: true, total: 4, name: false}}),
       "Scanning assets true/4: false", "数字/布尔统一 String 化");
expect(trLog({message: "扫描资源 1/2：a"}),
       "Scanning assets 1/2: a", "旧字符串走 LOG_PATTERNS 回退");
expect(trLog({message: "扫描资源 1/2：a", code: "unknown_code"}),
       "Scanning assets 1/2: a", "未知 code 仍走 LOG_PATTERNS 回退");
expect(trLog({message: "没有匹配的中文"}),
       "没有匹配的中文", "正则不命中时原文返回");
delete LOG_CODES.scan_progress.fr;
LANG = "fr";
expect(trLog({message: "ignored", code: "scan_progress",
              params: {i: 1, total: 2, name: "a"}}),
       "Scanning assets 1/2: a", "缺该语言时回退英文");
console.log("trlog-ok");
"""
    code = 'globalThis.navigator = { language: "en" };\n' + js + harness
    r = subprocess.run([node], input=code, capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode == 0, f"Node 运行 trLog 失败：{r.stdout}\n{r.stderr}"
    assert "trlog-ok" in r.stdout
