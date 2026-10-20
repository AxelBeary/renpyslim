"""v0.17 WP5 前端功能闸：关键 DOM/JS 契约存在 + 新增文案八语齐全。

只做静态断言（不启动服务、不跑浏览器），避免脆弱的整段 HTML 比对：
- 关键元素 id / 按钮 / 函数名存在（opt_only、导出、reveal、APK 预分析、
  任务历史、危险确认弹窗）；
- app.js / i18n.js 里 t()/tf() 引用的字面键在八本字典里都存在；
- v0.17 新增键集与 hist_states 的六种状态八语齐全。
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "web" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
APP = (STATIC / "app.js").read_text(encoding="utf-8")
I18N = (STATIC / "i18n.js").read_text(encoding="utf-8")

LANGS = ("zh", "en", "ru", "es", "pt", "tr", "de", "fr")
_STR_CHARS = "\"'`"
_KEY_FORM = re.compile(r"[a-z_$][a-z_0-9$]*")

# WP5 新增的顶层文案键（八本字典必须都有）
NEW_V017_KEYS = {
    "opt_only", "plat_none", "report_truncated", "warn_more",
    "export_csv", "export_json", "export_fail",
    "export_name_report", "export_name_apk",
    "open_dir", "locate_file", "locate_input", "locate_apk", "locate_output",
    "locate_deliver", "locate_changelog", "locate_report", "locate_keystore",
    "locate_charlist", "locate_font", "copy_path", "copy_ok", "copy_fail",
    "reveal_fail", "api_too_old", "unknown_error", "path_actions", "r_only_opt",
    "apk_report_title", "apk_report_desc", "apk_analyze_hint",
    "apk_stat_size", "apk_stat_optimizable", "apk_stat_est",
    "apk_stat_est_remap", "apk_stat_meta", "apk_analyzing",
    "apk_analyze_err", "apk_signed_warn", "apk_not_renpy",
    "confirm", "btn_cancel", "inplace_confirm_title", "inplace_confirm_msg",
    "inplace_confirm_ok",
    "hist_title", "hist_desc", "hist_refresh", "hist_clear", "hist_empty",
    "hist_loading", "hist_fail", "hist_cleared", "hist_clear_title",
    "hist_clear_msg", "hist_clear_ok", "hist_locate", "hist_saved",
    "hist_untitled", "hist_states",
}

HISTORY_STATUSES = ("running", "queued", "interrupted", "done",
                    "canceled", "error", "unknown")


def _skip_string(text: str, i: int) -> int:
    quote = text[i]
    i += 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i
        i += 1
    return i


def _lang_block(src: str, lang: str) -> str:
    marker = f"\n{lang}: {{"
    start = src.index(marker) + len(marker) - 1
    depth = 0
    i = start
    while i < len(src):
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
    keys = set()
    depth = 0
    i = 0
    while i < len(block):
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
            end = j + 1
            while j >= 0 and (block[j].isalnum() or block[j] in "_$"):
                j -= 1
            key = block[j + 1:end]
            if key and _KEY_FORM.fullmatch(key):
                keys.add(key)
        i += 1
    return keys


def _dicts() -> dict:
    return {lang: _lang_block(I18N, lang) for lang in LANGS}


def _literal_t_keys(src: str) -> set:
    """t("key") / tf("key", ...) 的字面键；动态 t(key) 不算。"""
    return set(re.findall(r'\b(?:t|tf)\(\s*"([a-z_$][a-z_0-9$]*)"', src))


def test_key_dom_hooks_present():
    needles = [
        'id="opt_only"',
        'id="onlywrap"',
        'id="platwarn"',
        'id="reportactions"',
        'id="reportcount"',
        'onclick="exportReport(\'project\',\'csv\')"',
        'onclick="exportReport(\'project\',\'json\')"',
        'id="apkanalyze"',
        'onclick="analyzeApk()"',
        'id="apkanalyzeinfo"',
        'id="apkreportcard"',
        'id="apkreporttable"',
        'id="apkreportactions"',
        'id="historycard"',
        'id="histlist"',
        'id="histstate"',
        'id="modalroot"',
        'role="dialog"',
        'aria-modal="true"',
        'id="modalcancel"',
        'id="modalconfirm"',
        'id="toast"',
        'role="status"',
        'aria-live="polite"',
    ]
    missing = [n for n in needles if n not in HTML]
    assert not missing, f"index.html 缺少 v0.17 关键钩子：{missing}"


def test_key_js_functions_present():
    needles = [
        "function confirmDanger",
        "function closeModal",
        "function revealPath",
        "function copyPath",
        "function toast",
        "function exportReport",
        "function reportToCSV",
        "async function analyzeApk",
        "async function pollApkAnalyze",
        "function renderApkReport",
        "async function loadHistory",
        "function renderHistory",
        "async function clearHistory",
        '"/api/reveal"',
        '"/api/history?limit=20"',
        '"/api/history", undefined, "DELETE"',
        'mode: "apk"',
        'mode: "project", options: opts',
        "if (opts.in_place)",
        "await confirmDanger(",
        "r.entries.slice(0, 300)",
        'if (e.key === "Escape")',
    ]
    missing = [n for n in needles if n not in APP]
    assert not missing, f"app.js 缺少 v0.17 关键实现：{missing}"


def test_export_uses_full_entries_not_truncated():
    # 表格只渲染前 300 行，但导出必须走全量 entries
    assert "r.entries.slice(0, 300)" in APP
    assert "for (const e of r.entries || [])" in APP
    assert 'JSON.stringify(r, null, 2)' in APP
    assert '"\\ufeff" + reportToCSV(r)' in APP


def test_new_v017_keys_in_every_language():
    dicts = _dicts()
    for lang, block in dicts.items():
        keys = _top_keys(block)
        missing = NEW_V017_KEYS - keys
        assert not missing, f"{lang} 字典缺 v0.17 新增键：{sorted(missing)}"


def test_all_literal_keys_in_app_and_i18n_exist_in_every_language():
    used = _literal_t_keys(APP) | _literal_t_keys(I18N)
    assert {"confirm", "hist_states", "path_actions", "hist_saved"} <= used
    dicts = _dicts()
    for lang, block in dicts.items():
        missing = used - _top_keys(block)
        assert not missing, f"{lang} 字典缺 app.js/i18n.js 引用的键：{sorted(missing)}"


def test_history_status_words_in_every_language():
    dicts = _dicts()
    for lang, block in dicts.items():
        m = re.search(r"hist_states:\s*\{([^}]*)\}", block)
        assert m, f"{lang} 字典没有 hist_states"
        body = m.group(1)
        missing = [s for s in HISTORY_STATUSES if not re.search(rf"\b{s}\s*:", body)]
        assert not missing, f"{lang} 的 hist_states 缺状态：{missing}"
