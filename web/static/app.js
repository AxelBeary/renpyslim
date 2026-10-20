"use strict";

/* ======================================================================
   界面业务逻辑：主题、导航、分析/优化/APK/字体流程与结果渲染。
   文案与日志翻译在 i18n.js，样式在 styles.css。
   ====================================================================== */

/* ===================== 全局状态 ===================== */
let MODE = "project";          // 工作流模式：project / dist
let VIEW = "project";          // 当前视图：project / dist / font / settings

let REPORT = null;             // 最近一次分析报告 {report, mode, root}
let LAST_RESULT = null;        // 最近一次执行结果（切语言时重渲染）
let LAST_CHARLIST = null;
let APK_REPORT = null;         // APK 预分析结果 {report, apk, root}
let JOB = null, LOGIDX = 0, JOB_GEN = 0, JOB_RUNNING = false;
let ANALYZE_JOB = null, ANALYZE_IDX = 0, ANALYZE_START = 0,
    ANALYZE_GEN = 0, ANALYZE_BUSY = false;
let APK_ANALYZE_JOB = null, APK_ANALYZE_IDX = 0, APK_ANALYZE_START = 0,
    APK_ANALYZE_GEN = 0, APK_ANALYZE_BUSY = false;
let FS_JOB = null, FS_IDX = 0, FS_RESULT = null, FS_GEN = 0;
let HISTORY = null;            // null = 还没加载过；[] = 加载过且为空
let MODAL = { resolve: null, lastFocus: null };
let TOAST_TIMER = null;

async function api(url, body, method) {
  /* 审核修复（中-29）：网络异常（服务退出/点了退出工具）捕下来
     返回统一失败结构，不再变成未捕获 rejection 断掉轮询链 */
  try {
    let opts;
    if (body !== undefined || method) {
      opts = { method: method || "POST" };
      if (body !== undefined) {
        opts.headers = { "Content-Type": "application/json" };
        opts.body = JSON.stringify(body);
      }
    }
    const r = await fetch(url, opts);
    return await r.json();
  } catch (e) {
    return { ok: false, error: "与后台服务的连接已断开", _conn_lost: true };
  }
}

/* 后台返回的统一错误文案：旧版本后台缺接口时给"升级"人话，而不是 undefined */
function apiErr(d, fallback) {
  if (d && typeof d.error === "string" && d.error) return d.error;
  /* 旧后台缺接口时（404/405 落到静态文件路由）没有 ok 字段：统一提示升级 */
  if (d && d.ok === undefined) return t("api_too_old");
  if (d && typeof d.detail === "string" && d.detail) return d.detail;
  return fallback || t("unknown_error");
}

/* ===================== 主题与语言 ===================== */
function applyThemeBtn() {
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  const btn = document.getElementById("themebtn");
  btn.textContent = dark ? "☀️" : "🌙";
  const label = t(dark ? "theme_to_light" : "theme_to_dark");
  btn.setAttribute("aria-label", label);
  btn.title = label;
}
function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme");
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("rps-theme", next); } catch (e) {}
  applyThemeBtn();
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", e => {
  if (!localStorage.getItem("rps-theme")) {
    document.documentElement.setAttribute("data-theme", e.matches ? "dark" : "light");
    applyThemeBtn();
  }
});

function setLang(l) {
  LANG = l;
  try { localStorage.setItem("rps-lang", l); } catch (e) {}
  applyLang();
}
function applyLang() {
  document.documentElement.setAttribute("lang", ({ zh: "zh-CN", pt: "pt-BR" })[LANG] || LANG);
  document.querySelectorAll("[data-i18n]").forEach(el => {
    const v = I18N[LANG][el.getAttribute("data-i18n")];
    if (v !== undefined) el.innerHTML = v;
  });
  document.querySelectorAll("[data-i18n-ph]").forEach(el => {
    const v = I18N[LANG][el.getAttribute("data-i18n-ph")];
    if (v !== undefined) {
      el.placeholder = v;
      /* 输入框没有显式 label，placeholder 同时充当可访问名（随语言同步） */
      el.setAttribute("aria-label", v);
    }
  });
  const sel = document.getElementById("langsel");
  if (sel && sel.value !== LANG) sel.value = LANG;
  /* 图标按钮与窄屏导航的可访问名：文案来自已翻译的 span */
  const langLabel = t("lang_switch");
  if (sel) { sel.setAttribute("aria-label", langLabel); sel.title = langLabel; }
  const quitBtn = document.getElementById("quitnarrow");
  if (quitBtn) { quitBtn.setAttribute("aria-label", t("quit")); quitBtn.title = t("quit"); }
  document.querySelectorAll(".nav-item").forEach(n => {
    const span = n.querySelector("span");
    if (!span) return;
    const label = span.textContent.trim();
    n.setAttribute("aria-label", label);
    n.title = label;
  });
  applyThemeBtn();
  /* 动态内容跟随语言重渲染 */
  applyViewText();
  renderEnv();
  renderRecent();
  /* 执行状态文案：已完成的任务切语言后同步重绘（防中文残留） */
  const rs = document.getElementById("runstatus");
  const rsTxt = rs ? rs.textContent : "";
  if (rs && ["完成", "Done"].includes(rsTxt)) rs.textContent = t("st_done");
  if (REPORT) renderReport(REPORT.report, REPORT.mode);
  if (APK_REPORT) renderApkReport();
  if (HISTORY) renderHistory();
  else if (VIEW === "settings") loadHistory();
  if (LAST_RESULT) renderResult(LAST_RESULT);
  if (FS_RESULT) renderFontResult(FS_RESULT);
}

/* ===================== 导航 ===================== */
function applyViewText() {
  const key = { project: "title_project", dist: "title_dist", apk: "title_apk",
                font: "title_font", settings: "title_settings" }[VIEW];
  document.getElementById("viewtitle").textContent = t(key);
  const isP = MODE === "project";
  document.getElementById("s1title").textContent = isP ? t("s1_title_p") : t("s1_title_d");
  document.getElementById("s1desc").textContent = isP ? t("s1_desc_p") : t("s1_desc_d");
}
function go(v) {
  const prev = VIEW;
  VIEW = v;
  document.querySelectorAll(".nav-item").forEach(n => {
    const on = n.getAttribute("data-view") === v;
    n.classList.toggle("active", on);
    if (on) n.setAttribute("aria-current", "page");
    else n.removeAttribute("aria-current");
  });
  const wf = v === "project" || v === "dist";
  document.getElementById("view-workflow").classList.toggle("hidden", !wf);
  document.getElementById("view-apk").classList.toggle("hidden", v !== "apk");
  document.getElementById("view-font").classList.toggle("hidden", v !== "font");
  document.getElementById("view-settings").classList.toggle("hidden", v !== "settings");
  if (wf && v !== prev) {           // project ↔ dist 切换：重置工作流
    MODE = v;
    REPORT = null; LAST_RESULT = null;
    /* 审核修复（中-26）：任务还在跑时保留执行卡片（轮询链继续），
       否则进度消失但后台任务还在跑，用户以为死了再起一个 */
    for (const id of ["reportcard", "optcard"])
      document.getElementById(id).classList.add("hidden");
    if (!JOB_RUNNING)
      document.getElementById("runcard").classList.add("hidden");
    document.getElementById("analyzewarn").innerHTML = "";
    applyModeUI();
  }
  applyViewText();
  if (v === "settings") loadHistory();   // 进入设置时刷新任务历史
}
/* ===================== 环境检测 / 更新提醒 ===================== */
let ENV = null;
async function loadEnv() {
  const d = await api("/api/env");
  ENV = d;
  document.getElementById("footver").textContent = "v" + (d.version || "?");
  if (d.environment && d.environment.sdk_path)
    document.getElementById("sdkpath").value = d.environment.sdk_path;
  renderEnv();
  api("/api/update").then(u => {
    if (u && u.info && u.info.update_available) {
      const chip = document.getElementById("updchip");
      chip.href = u.info.url; chip.textContent = tf("upd_found", { v: u.info.latest });
      chip.classList.remove("hidden");
      document.getElementById("updbox").innerHTML =
        `<div class="box warn"><a href="${esc(u.info.url)}" target="_blank" ` +
        `style="color:inherit">${tf("upd_found", { v: u.info.latest })}</a></div>`;
    }
  }).catch(() => {});
}
function renderEnv() {
  if (!ENV) return;
  const e = ENV.environment;
  const mk = (ok, label, detail) =>
    `<span class="envlight"><i class="dot ${ok ? "ok" : "miss"}"></i>` +
    `${esc(label)}${detail ? ": " + esc(detail) : ""}</span>`;
  document.getElementById("envlights").innerHTML =
    mk(!!e.sdk_version, t("env_sdk"), e.sdk_version || t("env_sdk_miss")) +
    mk(!!e.ffmpeg, t("env_ffmpeg"), e.ffmpeg ? "" : t("env_ff_miss")) +
    mk(!!e.java, t("env_java"), e.java ? "" : t("env_java_miss"));
  const st = (ok, name, detail) =>
    `<div class="stat"><b style="color:${ok ? "var(--ok-ink)" : "var(--warn-ink)"}">` +
    `${ok ? "✓" : "✗"}</b><span>${esc(name)}${detail ? " · " + esc(detail) : ""}</span></div>`;
  document.getElementById("envstats").innerHTML =
    st(!!e.sdk_version, "Ren'Py SDK", e.sdk_version || t("env_sdk_miss")) +
    st(!!e.ffmpeg, "FFmpeg", e.ffmpeg ? t("env_ready") : t("env_ff_miss")) +
    st(!!e.java, "Java", e.java ? t("env_ready") : t("env_java_miss"));
}

/* ===================== 浏览选择框 / 最近路径 ===================== */
function isArchiveName(s) { return /\.(zip|7z|rar)$/i.test(s.trim()); }
function isApkName(s) { return /\.apk$/i.test(s.trim()); }

/* 按文件类型把路径送进正确的入口（小白不用自己猜该用哪个页面） */
function routePath(p) {
  if (isApkName(p)) { go("apk"); document.getElementById("apk_path").value = p; return true; }
  return false;
}

async function browse(kind) {
  const d = await api("/api/browse?kind=" + kind);
  if (!d.ok) { alert(d.error || t("browse_fail")); return; }
  if (!d.path) return;                 // 用户取消了选择
  if (routePath(d.path)) return;
  document.getElementById("path").value = d.path;
  if (isArchiveName(d.path) && MODE !== "dist") go("dist");
}
async function browseFor(inputId, kind) {
  const d = await api("/api/browse?kind=" + kind);
  if (!d.ok) { alert(d.error || t("browse_fail")); return; }
  if (!d.path) return;
  const el = document.getElementById(inputId);
  el.value = el.value && kind !== "font" ? el.value + "," + d.path : d.path;
}

let RECENT = [];
async function loadRecent() {
  const d = await api("/api/recent");
  RECENT = (d && d.ok) ? d.recent : [];
  renderRecent();
}
function renderRecent() {
  const row = document.getElementById("recentrow");
  if (!RECENT.length) { row.classList.add("hidden"); return; }
  row.classList.remove("hidden");
  document.getElementById("recentbox").innerHTML = RECENT.map(p =>
    `<span class="chip" title="${esc(p)}" onclick="useRecent(this)" ` +
    `data-path="${esc(p)}" style="margin-right:6px">` +
    `${esc(p.length > 34 ? "…" + p.slice(-33) : p)}</span>`).join("");
}
function useRecent(el) {
  const p = el.getAttribute("data-path");
  if (routePath(p)) return;
  document.getElementById("path").value = p;
  if (isArchiveName(p) && MODE !== "dist") go("dist");
}

/* ===================== 分析流程 ===================== */
async function analyze() {
  /* 审核修复（中-26）：防连击——上一次分析还在跑时不起第二个 */
  if (ANALYZE_BUSY) return;
  const path = document.getElementById("path").value.trim();
  if (!path) { alert(t("fill_path")); return; }
  document.getElementById("analyzewarn").innerHTML =
    `<div class="box ok"><div id="analystate">${t("analyzing")}</div>` +
    `<div class="logbox" id="anlog" style="margin-top:8px;max-height:110px"></div></div>`;
  const d = await api("/api/analyze", { path, mode: MODE,
    password: document.getElementById("arc_pwd").value || null });
  if (!d.ok) {
    document.getElementById("analyzewarn").innerHTML =
      `<div class="box err">${esc(apiErr(d))}</div>`;
    return;
  }
  ANALYZE_BUSY = true;
  ANALYZE_GEN++;                      // 审核修复（中-26）：作废旧轮询链
  const gen = ANALYZE_GEN;
  ANALYZE_JOB = d.job; ANALYZE_IDX = 0; ANALYZE_START = Date.now();
  pollAnalyze(path, gen);
}

async function pollAnalyze(path, gen) {
  if (gen !== ANALYZE_GEN) return;    // 旧轮询链已被作废
  const d = await api(`/api/job/${ANALYZE_JOB}?since=${ANALYZE_IDX}`);
  if (gen !== ANALYZE_GEN) return;
  if (!d.ok) {
    /* 审核修复（中-29）：任务被清理/断连时给出明确状态，
       不再静默卡死在"分析中" */
    ANALYZE_BUSY = false;
    document.getElementById("analyzewarn").innerHTML =
      `<div class="box err">${esc(apiErr(d))}</div>`;
    return;
  }
  const logEl = document.getElementById("anlog");
  const stateEl = document.getElementById("analystate");
  const lines = [];
  for (const l of d.logs) {
    ANALYZE_IDX++;
    lines.push(`[${t("stages")[l.stage] || l.stage}] ${trLog(l.message)}`);
    if (stateEl) stateEl.textContent = trLog(l.message);
  }
  if (logEl && lines.length) {
    const all = (logEl.textContent + lines.join("\n") + "\n").split("\n").filter(Boolean);
    logEl.textContent = all.slice(-6).join("\n");
    logEl.scrollTop = logEl.scrollHeight;
  }
  if (d.status === "running") { setTimeout(() => pollAnalyze(path, gen), 300); return; }
  ANALYZE_BUSY = false;
  if (d.status === "error") {
    document.getElementById("analyzewarn").innerHTML =
      `<div class="box err">${esc(d.error)}<br>${t("analyze_err_next")}</div>`;
    return;
  }
  /* 小项目扫描太快：进度框至少停留 1 秒，让用户看见“分析完成” */
  const stateEl2 = document.getElementById("analystate");
  if (stateEl2) stateEl2.textContent = t("analyze_done");
  const wait = Math.max(0, 1000 - (Date.now() - ANALYZE_START));
  setTimeout(() => {
    if (gen !== ANALYZE_GEN) return;   // 期间起了新分析，旧链不接管界面
    api("/api/recent", { path }).then(r => { if (r.ok) { RECENT = r.recent; renderRecent(); } });
    REPORT = { report: d.result.report, mode: d.result.mode,
               root: d.result.report.root || path };
    LAST_CHARLIST = d.result.charlist || null;
    renderReport(d.result.report, d.result.mode);
    document.getElementById("analyzewarn").innerHTML = "";
    document.getElementById("optcard").classList.remove("hidden");
  }, wait);
}

function defaultReportStats(r) {
  const est = r.entries.reduce((s, e) => s + (e.est_saving || 0), 0);
  let stats =
    `<div class="stat"><b>${r.entries.length}</b><span>${t("stat_assets")}</span></div>` +
    `<div class="stat"><b>${fmtSize(r.total_size)}</b><span>${t("stat_size")}</span></div>`;
  if (est) stats += `<div class="stat big"><b>${fmtSize(est)}</b><span>${t("stat_est")}</span></div>`;
  if (r.charset_size) stats +=
    `<div class="stat"><b>${r.charset_size}</b><span>${t("stat_chars")}</span></div>`;
  return stats;
}

/* 警告条：只显示前 10 条，超出时注明完整内容在 JSON 导出里 */
function warnBox(list) {
  if (!list || !list.length) return "";
  const shown = list.slice(0, 10);
  return `<div class="box warn">${warnNote()}${shown.map(esc).join("<br>")}` +
    (list.length > 10
      ? `<div class="note" style="margin-top:6px">${t("warn_more")}</div>` : "") +
    `</div>`;
}

/* 报告渲染：工程报告与 APK 预分析共用；targets 可换目标 DOM，
   statsHtml 可替换统计卡（APK 用体积/可优化数/两档预估）。 */
function renderReport(r, mode, targets) {
  const tg = Object.assign({
    card: "reportcard", stats: "reportstats", table: "reporttable",
    warn: "reportwarn", count: "reportcount", actions: "reportactions",
  }, targets || {});
  document.getElementById(tg.card).classList.remove("hidden");
  document.getElementById(tg.stats).innerHTML = tg.statsHtml || defaultReportStats(r);

  let html = `<tr><th>${t("th_pri")}</th><th>${t("th_kind")}</th><th>${t("th_file")}</th>` +
    `<th>${t("th_size")}</th><th>${t("th_issue")}</th></tr>`;
  for (const e of r.entries.slice(0, 300)) {
    const a = e.asset;
    const issues = e.issues.map(i => tf("issue_tpl", { m: esc(i.message), s: esc(i.suggestion) })).join("<br>");
    html += `<tr><td class="pri-${e.priority}">${t("pri")[e.priority]}</td>` +
      `<td><span class="badge">${t("kinds")[a.kind] || a.kind}` +
      `${a.in_rpa ? t("kind_rpa") : ""}</span></td>` +
      `<td style="word-break:break-all">${esc(a.rel)}</td><td>${fmtSize(a.size)}</td>` +
      `<td>${issues || "—"}</td></tr>`;
  }
  document.getElementById(tg.table).innerHTML = html;

  const countEl = document.getElementById(tg.count);
  if (countEl) {
    countEl.textContent = r.entries.length > 300
      ? tf("report_truncated", { n: r.entries.length }) : "";
  }

  let warnHtml = tg.extraWarn || "";
  warnHtml += warnBox(r.warnings);
  if (mode === "project" && r.charset_size && LAST_CHARLIST)
    warnHtml += `<div class="row" style="margin-top:10px">` +
      `<button type="button" class="btn ghost small" onclick="downloadCharlist()">${t("r_charlist")}</button></div>`;
  document.getElementById(tg.warn).innerHTML = warnHtml;

  if (tg.actions) {
    const root = r.root;
    const acts = [];
    if (root) {
      const isFile = isApkName(root) || isArchiveName(root);
      acts.push(isFile
        ? { kind: "file", path: root, label: t(mode === "apk" ? "locate_apk" : "locate_input") }
        : { kind: "dir", path: root });
      acts.push({ kind: "copy", path: root });
    }
    const box = document.getElementById(tg.actions);
    box.innerHTML = pathActionButtons(acts);
    box.setAttribute("aria-label", t("path_actions"));
    box.classList.toggle("hidden", !box.innerHTML);
  }
}

/* ===================== 路径动作（打开/定位/复制） ===================== */
function pathActionButtons(items) {
  if (!items || !items.length) return "";
  let html = `<div class="row actions" role="group" aria-label="${esc(t("path_actions"))}">`;
  let has = false;
  for (const it of items) {
    if (!it || !it.path) continue;
    const label = it.label ||
      t(it.kind === "copy" ? "copy_path" : it.kind === "file" ? "locate_file" : "open_dir");
    const attr = it.kind === "copy" ? "data-copy"
      : it.kind === "file" ? "data-locate" : "data-open-dir";
    html += `<button type="button" class="btn ghost small" ${attr}="${esc(it.path)}" ` +
      `aria-label="${esc(label)}">${esc(label)}</button>`;
    has = true;
  }
  return has ? html + `</div>` : "";
}

async function revealPath(path, reveal) {
  if (!path) return;
  const d = await api("/api/reveal", { path, reveal: !!reveal });
  if (!d || d.ok !== true) toast(apiErr(d, t("reveal_fail")));
}

async function copyPath(text) {
  if (!text) return;
  let ok = false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      ok = true;
    }
  } catch (e) { ok = false; }
  if (!ok) {
    /* 剪贴板 API 在非安全上下文/旧浏览器不可用：退回隐藏 textarea + execCommand */
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      ok = document.execCommand("copy");
      document.body.removeChild(ta);
    } catch (e) { ok = false; }
  }
  toast(ok ? t("copy_ok") : t("copy_fail"));
}

function toast(msg) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(TOAST_TIMER);
  TOAST_TIMER = setTimeout(() => el.classList.add("hidden"), 2800);
}

/* 结果/报告统一挂在 document 上：动态按钮只带 data-*，不拼 onclick */
document.addEventListener("click", e => {
  const btn = e.target.closest("button[data-copy],button[data-open-dir],button[data-locate]");
  if (!btn) return;
  if (btn.hasAttribute("data-copy")) copyPath(btn.getAttribute("data-copy"));
  else if (btn.hasAttribute("data-open-dir")) revealPath(btn.getAttribute("data-open-dir"), false);
  else revealPath(btn.getAttribute("data-locate"), true);
});

/* ===================== 报告导出（全量，不受表格 300 行截断影响） ===================== */
function csvCell(v) {
  const s = v == null ? "" : String(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function reportToCSV(r) {
  const head = ["priority", "kind", "rel", "size", "est_saving", "issues"];
  const rows = [head.join(",")];
  for (const e of r.entries || []) {
    const a = e.asset || {};
    const issues = (e.issues || [])
      .map(i => [i.message, i.suggestion].filter(Boolean).join(" ")).join(" | ");
    rows.push([
      e.priority, a.kind, a.rel, a.size,
      e.est_saving == null ? "" : e.est_saving, issues,
    ].map(csvCell).join(","));
  }
  return rows.join("\r\n");
}

function downloadText(text, filename, mime) {
  const blob = new Blob([text], { type: mime + ";charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function reportFor(scope) {
  if (scope === "apk") return APK_REPORT ? APK_REPORT.report : null;
  return REPORT ? REPORT.report : null;
}

function exportReport(scope, format) {
  const r = reportFor(scope);
  if (!r || !Array.isArray(r.entries)) { toast(t("export_fail")); return; }
  const d = new Date();
  const pad = n => String(n).padStart(2, "0");
  const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}` +
    `-${pad(d.getHours())}${pad(d.getMinutes())}`;
  const base = t(scope === "apk" ? "export_name_apk" : "export_name_report") + "-" + stamp;
  if (format === "json") {
    downloadText(JSON.stringify(r, null, 2), base + ".json", "application/json");
  } else {
    /* UTF-8 BOM：Excel 直接双击打开不乱码 */
    downloadText("\ufeff" + reportToCSV(r), base + ".csv", "text/csv");
  }
}

function downloadCharlist() {
  if (!LAST_CHARLIST) return;
  const a = document.createElement("a");
  const blob = new Blob([LAST_CHARLIST], { type: "text/plain;charset=utf-8" });
  a.href = URL.createObjectURL(blob);
  a.download = LANG === "zh" ? "使用字符清单.txt" : "charset-used.txt";
  a.click();
  URL.revokeObjectURL(a.href);
}
/* ===================== 选项收集 ===================== */
function collectOptions() {
  return {
    preset: document.getElementById("preset").value,
    do_images: document.getElementById("do_images").checked,
    do_audio: document.getElementById("do_audio").checked,
    do_fonts: document.getElementById("do_fonts").checked,
    convert_png_webp: document.getElementById("convert_webp").checked,
    in_place: document.getElementById("in_place").checked,
    delete_unreferenced: document.getElementById("del_unref").checked,
    quarantine_unused: document.getElementById("q_unused").checked,
    png_quant: document.getElementById("opt_quant").checked,
    do_videos: document.getElementById("opt_videos").checked,
    experimental_av1: document.getElementById("opt_av1").checked,
    experimental_remap: document.getElementById("opt_remap").checked,
    experimental_decompile: document.getElementById("opt_decompile").checked,
    use_cache: !document.getElementById("opt_nocache").checked,
    charset: {
      base_latin: document.getElementById("cs_latin").checked,
      cjk_punct: document.getElementById("cs_cjk").checked,
      fullwidth: document.getElementById("cs_full").checked,
      kana: document.getElementById("cs_kana").checked,
      extra_chars: document.getElementById("cs_extra").value,
    },
  };
}
document.getElementById("in_place").addEventListener("change", e =>
  document.getElementById("inplacewarn").classList.toggle("hidden", !e.target.checked));

/* ===================== 执行流程 ===================== */
function scrollToRunCard() {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.getElementById("runcard").scrollIntoView({ behavior: reduce ? "auto" : "smooth" });
}
async function runJob() {
  const path = document.getElementById("path").value.trim();
  if (!path) { alert(t("fill_path")); return; }
  const opts = collectOptions();
  const onlyOpt = MODE === "project" && document.getElementById("opt_only").checked;

  /* 工程模式：要打包就必须选平台；只优化则不需要（也不要求 SDK） */
  const platforms = [];
  if (MODE === "project" && !onlyOpt) {
    if (document.getElementById("pl_pc").checked) platforms.push("pc");
    if (document.getElementById("pl_mac").checked) platforms.push("mac");
    if (document.getElementById("pl_android").checked) platforms.push("android");
    if (!platforms.length) {
      document.getElementById("runcard").classList.remove("hidden");
      document.getElementById("log").textContent = "";
      document.getElementById("runstatus").textContent = t("st_failed");
      document.getElementById("progressbar").classList.remove("ind");
      document.getElementById("resultarea").innerHTML =
        `<div class="box err">${t("plat_none")}</div>`;
      document.getElementById("runbtn").disabled = false;
      scrollToRunCard();
      return;
    }
  }

  /* 直接改原件是高危操作：提交前必须走自定义确认框（默认焦点在取消） */
  if (opts.in_place) {
    const okay = await confirmDanger({
      title: t("inplace_confirm_title"),
      message: t("inplace_confirm_msg"),
      confirmText: t("inplace_confirm_ok"),
    });
    if (!okay) return;
  }

  /* 审核修复（中-26）：起新任务先作废旧轮询链，防新旧链共写
     全局游标导致日志重复/丢条、"停止"打到新任务上 */
  JOB_GEN++;
  const gen = JOB_GEN;
  document.getElementById("runcard").classList.remove("hidden");
  document.getElementById("log").textContent = "";
  document.getElementById("resultarea").innerHTML = "";
  document.getElementById("runstatus").textContent = t("st_preparing");
  resetProgress();
  document.getElementById("runbtn").disabled = true;
  LAST_RESULT = null;
  LOGIDX = 0;
  scrollToRunCard();

  let d;
  const pwd = document.getElementById("arc_pwd").value || null;
  if (MODE === "project") {
    if (onlyOpt) {
      /* 只优化不打包：工程模式也走 /api/optimize，不需要 SDK */
      d = await api("/api/optimize", { path, mode: "project", options: opts });
    } else {
      d = await api("/api/full", { path, platforms, options: opts,
        archive_rpa: document.getElementById("rpa_pack").checked });
    }
  } else {
    d = await api("/api/optimize", { path, mode: "dist", options: opts, password: pwd });
  }
  if (gen !== JOB_GEN) return;        // 等待期间又起了新任务
  if (!d.ok) {
    document.getElementById("resultarea").innerHTML =
      `<div class="box err">${esc(apiErr(d))}</div>`;
    document.getElementById("runbtn").disabled = false;
    setProgress(100); document.getElementById("progressbar").classList.remove("ind");
    return;
  }
  JOB = d.job;
  saveJob(d.job);
  JOB_RUNNING = true;
  const cb = document.getElementById("cancelbtn");
  cb.style.display = ""; cb.disabled = false;
  pollJob(gen);
}

/* 进度条：日志里出现 i/total 就按真实百分比走，否则滑动动画 */
function resetProgress() {
  const bar = document.getElementById("progressbar");
  bar.classList.add("ind");
  document.getElementById("progressfill").style.width = "0";
}
function setProgress(pct) {
  document.getElementById("progressfill").style.width = pct + "%";
}
function tickProgress(msg) {
  const m = msg.match(/(\d+)\s*\/\s*(\d+)/);
  if (m) {
    const cur = parseInt(m[1]), tot = parseInt(m[2]);
    if (tot > 0 && cur <= tot) {
      document.getElementById("progressbar").classList.remove("ind");
      setProgress(Math.round(cur / tot * 100));
    }
  }
}

async function pollJob(gen) {
  if (gen !== JOB_GEN) return;        // 审核修复（中-26）：旧链作废
  const d = await api(`/api/job/${JOB}?since=${LOGIDX}`);
  if (gen !== JOB_GEN) return;
  if (!d.ok) {
    /* 审核修复（中-29）：任务被清理/断连时恢复按钮并给出状态，
       不再永久卡在“执行中”（高-2 的前端放大器） */
    JOB_RUNNING = false;
    clearSavedJob();
    loadHistory();
    document.getElementById("cancelbtn").style.display = "none";
    document.getElementById("progressbar").classList.remove("ind");
    document.getElementById("runstatus").textContent =
      d._conn_lost ? d.error : t("st_failed");
    document.getElementById("resultarea").innerHTML =
      `<div class="box err">${esc(d.error || "")}</div>`;
    document.getElementById("runbtn").disabled = false;
    document.getElementById("apkrunbtn").disabled = false;
    return;
  }
  const log = document.getElementById("log");
  for (const l of d.logs) {
    log.textContent += `[${t("stages")[l.stage] || l.stage}] ${trLog(l.message)}\n`;
    LOGIDX++;
    tickProgress(l.message);
  }
  log.scrollTop = log.scrollHeight;
  if (d.status === "running") {
    document.getElementById("runstatus").textContent =
      d.cancel_requested ? t("st_stopping") : t("st_running");
    setTimeout(() => pollJob(gen), 800);
    return;
  }
  /* 任务队列（用户要求 2026-08-17）：排队中不是终态，继续等位 */
  if (d.status === "queued") {
    JOB_RUNNING = true;
    document.getElementById("runstatus").textContent = t("st_queued");
    document.getElementById("cancelbtn").style.display = "";
    document.getElementById("cancelbtn").disabled = false;
    setTimeout(() => pollJob(gen), 800);
    return;
  }
  JOB_RUNNING = false;
  clearSavedJob();
  loadHistory();                       // 任务结束：刷新设置页的历史卡
  document.getElementById("cancelbtn").style.display = "none";
  document.getElementById("progressbar").classList.remove("ind");
  if (d.status === "canceled") {
    document.getElementById("runstatus").textContent = t("st_cancelled");
    document.getElementById("resultarea").innerHTML =
      `<div class="box warn">${esc(d.error || "")}</div>`;
    document.getElementById("runbtn").disabled = false;
    document.getElementById("apkrunbtn").disabled = false;
    return;
  }
  document.getElementById("runbtn").disabled = false;
  document.getElementById("apkrunbtn").disabled = false;
  if (d.status === "error") {
    document.getElementById("runstatus").textContent = t("st_failed");
    document.getElementById("resultarea").innerHTML =
      `<div class="box err">${esc(d.error)}<br>${t("err_next")}</div>`;
    return;
  }
  document.getElementById("runstatus").textContent = t("st_done");
  setProgress(100);
  LAST_RESULT = d.result;
  renderResult(d.result);
}

async function cancelJob() {
  if (!JOB) return;
  document.getElementById("cancelbtn").disabled = true;
  await api(`/api/job/${JOB}/cancel`, {});
}

/* ===================== 任务队列与断线重连（用户要求 2026-08-17） ===================== */
/* 任务编号存本地：关掉网页再打开，能接回还在跑/排队的任务 */
function saveJob(id) { try { localStorage.setItem("rps-job", id); } catch (e) {} }
function clearSavedJob() { try { localStorage.removeItem("rps-job"); } catch (e) {} }

function kindName(k) { const d = t("q_kinds"); return (d && d[k]) || k; }

/* 右下角队列面板：有执行中/排队中任务就自动出现，兼顾“提醒” */
async function refreshQueue() {
  const panel = document.getElementById("queuepanel");
  const d = await api("/api/jobs");
  if (!d.ok) { panel.classList.add("hidden"); return; }
  const active = (d.jobs || []).filter(j => j.status === "running" || j.status === "queued");
  if (!active.length) { panel.classList.add("hidden"); return; }
  panel.classList.remove("hidden");
  const states = t("q_states");
  document.getElementById("queuelist").innerHTML = active.map(j =>
    `<div class="qitem"><span>${esc(kindName(j.kind))} · ` +
    `<span class="qstate">${esc(states[j.status] || j.status)}</span></span>` +
    `<button class="btn ghost small" data-qid="${j.id}">${esc(t("cancel"))}</button></div>`).join("");
}

/* 断线重连：打开页面时后台还有执行中/排队中的任务，自动接回进度卡
   （优先接本页上次提交的那个；接回后轮询链自行跟进直到出结果） */
const RESUMABLE_KINDS = ["optimize", "full", "package", "slimapk"];
async function tryResumeJob() {
  const d = await api("/api/jobs");
  if (!d.ok) return;
  const alive = j => RESUMABLE_KINDS.includes(j.kind) &&
    (j.status === "running" || j.status === "queued");
  const jobs = d.jobs || [];
  let saved = null;
  try { saved = localStorage.getItem("rps-job"); } catch (e) {}
  const job = jobs.find(j => j.id === saved && alive(j)) ||
              jobs.find(j => alive(j) && j.status === "running") ||
              jobs.find(j => alive(j));
  if (!job) { if (saved) clearSavedJob(); return; }
  saveJob(job.id);
  JOB = job.id; LOGIDX = 0; JOB_GEN++;
  const gen = JOB_GEN;
  document.getElementById("runcard").classList.remove("hidden");
  document.getElementById("log").textContent = "";
  document.getElementById("resultarea").innerHTML = "";
  resetProgress();
  document.getElementById("runbtn").disabled = true;
  document.getElementById("apkrunbtn").disabled = true;
  document.getElementById("runstatus").textContent =
    job.status === "queued" ? t("st_queued") : t("st_resumed");
  JOB_RUNNING = true;
  /* APK 瘦身跑起来后引擎层停不下来，不显示无效停止键；排队中都能退队 */
  document.getElementById("cancelbtn").style.display =
    (job.kind === "slimapk" && job.status === "running") ? "none" : "";
  document.getElementById("cancelbtn").disabled = false;
  pollJob(gen);
}

/* ===================== 结果渲染 ===================== */
function optimizeActions(o) {
  const acts = [];
  if (!o) return "";
  if (o.working_dir) {
    acts.push({ kind: "dir", path: o.working_dir });
    acts.push({ kind: "copy", path: o.working_dir });
  }
  if (o.changelog) acts.push({ kind: "file", path: o.changelog, label: t("locate_changelog") });
  if (o.report) acts.push({ kind: "file", path: o.report, label: t("locate_report") });
  return pathActionButtons(acts);
}

function renderOptimizeBody(o, only) {
  const label = t("r_opt_done").split("：")[0].split(": ")[0];
  let html = `<div class="stats" style="grid-template-columns:repeat(3,1fr)">` +
    `<div class="stat big"><b>${fmtSize(o.saved_bytes)}</b><span>${esc(label)}</span></div>` +
    `<div class="stat"><b style="font-size:13px">${esc(o.working_dir)}</b><span>${t("r_workdir")}</span></div>` +
    `<div class="stat"><b style="font-size:13px">${esc(String(o.changelog))}</b><span>${t("r_changelog")}</span></div></div>`;
  if (o.validation && o.validation.ran) {
    html += o.validation.ok
      ? `<div class="box ok">${t("r_lint_ok")}</div>`
      : `<div class="box warn">${t("r_lint_head")}${esc(o.validation.summary)}` +
        (o.validation.suspects && o.validation.suspects.length
          ? "<br>" + o.validation.suspects.slice(0, 5).map(s => "· " + esc(s)).join("<br>") : "") +
        `</div>`;
  }
  const extra = [];
  if (o.junk && o.junk.removed.length)
    extra.push(tf("r_junk", { n: o.junk.removed.length, s: fmtSize(o.junk.freed_bytes) }));
  if (o.unused && o.unused.length)
    extra.push(tf("r_unused", { n: o.unused.length }) +
      (o.quarantined && o.quarantined.length
        ? tf("r_unused_q", { n: o.quarantined.length }) : t("r_unused_rep")));
  if (o.duplicates && o.duplicates.length)
    extra.push(tf("r_dup", { n: o.duplicates.length }));
  if (o.missing_glyphs && Object.keys(o.missing_glyphs).length)
    extra.push(tf("r_glyph", { n: Object.keys(o.missing_glyphs).length }));
  if (extra.length) html += `<div class="box ok">${t("r_extra_head")}${extra.map(esc).join("<br>")}</div>`;
  html += warnBox(o.warnings);
  if (only) html += `<div class="box warn">${t("r_only_opt")}</div>`;
  return html;
}

function renderPackageBody(p) {
  let html = "";
  if (p.built && p.built.length) {
    html += `<div class="box ok">${t("r_pkg_ok")}${esc(p.built.join("、"))}<br>` +
      (p.artifacts || []).map(a => `· ${esc(a.name)}（${fmtSize(a.size)}）`).join("<br>") +
      `<br>${t("r_pkg_out")}：${esc(p.destination)}</div>`;
  }
  for (const e of (p.errors || [])) html += `<div class="box err">${esc(e)}</div>`;
  const acts = [];
  if (p.destination) {
    acts.push({ kind: "dir", path: p.destination });
    acts.push({ kind: "copy", path: p.destination });
  }
  return html + pathActionButtons(acts);
}

function renderResult(res) {
  let html = "";
  if (res.saved_bytes !== undefined && res.changes !== undefined && !res.optimize) {
    /* APK 瘦身结果（APK 页与压缩包自动转发共用此渲染） */
    html += `<div class="stats" style="grid-template-columns:repeat(3,1fr)">` +
      `<div class="stat big"><b>${fmtSize(res.saved_bytes)}</b><span>${t("r_apk_saved")}</span></div>` +
      `<div class="stat"><b>${res.changes}</b><span>${t("r_apk_changes")}</span></div>` +
      `<div class="stat"><b style="font-size:13px;color:${res.signed ? "var(--ok-ink)" : "var(--warn-ink)"}">` +
      `${res.signed ? t("r_apk_signed_yes") : t("r_apk_signed_no")}</b><span>${t("r_apk_signed")}</span></div></div>`;
    html += `<div class="box ok">${t("r_apk_out")}：${esc(res.output)}` +
      (res.keystore
        ? `<br>${t("r_apk_ks")}：${esc(res.keystore.keystore)}` +
          `<br>${t("r_apk_ks_memo")}：${esc(res.keystore.memo)}`
        : "") + `</div>`;
    const acts = [];
    if (res.output) {
      acts.push({ kind: "file", path: res.output, label: t("locate_output") });
      acts.push({ kind: "copy", path: res.output });
    }
    if (res.keystore) {
      if (res.keystore.keystore)
        acts.push({ kind: "file", path: res.keystore.keystore, label: t("locate_keystore") });
      if (res.keystore.memo) acts.push({ kind: "copy", path: res.keystore.memo });
    }
    html += pathActionButtons(acts);
    html += warnBox(res.warnings);
    document.getElementById("resultarea").innerHTML = html;
    return;
  }
  if (res.optimize) {
    html += renderOptimizeBody(res.optimize, false);
    html += optimizeActions(res.optimize);
  } else if (res.mode === "project" && res.saved_bytes !== undefined) {
    /* 工程「只优化不打包」：结果结构与 /api/full 的 optimize 段一致 */
    html += renderOptimizeBody(res, true);
    html += optimizeActions(res);
  } else if (res.saved_bytes !== undefined) {
    const label = t("r_dist_done").split("：")[0].split(": ")[0];
    html += `<div class="stats" style="grid-template-columns:repeat(3,1fr)">` +
      `<div class="stat big"><b>${fmtSize(res.saved_bytes)}</b><span>${esc(label)}</span></div>` +
      `<div class="stat"><b style="font-size:13px">${esc(res.working_dir)}</b><span>${t("r_outdir")}</span></div>` +
      (res.archive_output
        ? `<div class="stat"><b style="font-size:13px">${esc(res.archive_output)}</b><span>${t("r_deliver")}</span></div>`
        : `<div class="stat"><b style="font-size:13px">${esc(String(res.changelog))}</b><span>${t("r_changelog")}</span></div>`)
      + `</div>`;
    if (res.has_rpy) html += `<div class="box ok">${t("r_has_rpy")}</div>`;
    html += warnBox(res.warnings);
    const acts = [];
    if (res.working_dir) {
      acts.push({ kind: "dir", path: res.working_dir });
      acts.push({ kind: "copy", path: res.working_dir });
    }
    if (res.archive_output) acts.push({ kind: "file", path: res.archive_output, label: t("locate_deliver") });
    if (res.changelog) acts.push({ kind: "file", path: res.changelog, label: t("locate_changelog") });
    if (res.report) acts.push({ kind: "file", path: res.report, label: t("locate_report") });
    html += pathActionButtons(acts);
  }
  const pkg = res.package || (res.built && res.destination ? res : null);
  if (pkg) html += renderPackageBody(pkg);
  document.getElementById("resultarea").innerHTML = html ||
    `<div class="box ok">${t("st_done")}</div>`;
}
/* ===================== 模式相关的显隐 ===================== */
function applyModeUI() {
  const isP = MODE === "project";
  document.getElementById("platarea").classList.toggle("hidden", !isP);
  document.getElementById("delwrap").classList.toggle("hidden", isP);
  document.getElementById("qwrap").classList.toggle("hidden", !isP);
  document.getElementById("pwdrow").classList.toggle("hidden", isP);
  /* “只优化不打包”只对工程有意义（成品/APK 本来就是本地处理） */
  document.getElementById("onlywrap").classList.toggle("hidden", !isP);
  updatePlatWarn();
}

/* 工程模式没选平台且没勾“只优化”：常驻提示，runJob 里也会拦下 */
function updatePlatWarn() {
  const isP = MODE === "project";
  const only = document.getElementById("opt_only").checked;
  const any = ["pl_pc", "pl_mac", "pl_android"]
    .some(id => document.getElementById(id).checked);
  document.getElementById("platwarn").classList.toggle("hidden", !(isP && !only && !any));
  document.getElementById("platarea").classList.toggle("is-off", isP && only);
}
["pl_pc", "pl_mac", "pl_android"].forEach(id =>
  document.getElementById(id).addEventListener("change", updatePlatWarn));
document.getElementById("opt_only").addEventListener("change", updatePlatWarn);

/* ===================== 字体瘦身（独立工具） ===================== */
async function runFontSlim() {
  const font = document.getElementById("fs_font").value.trim();
  const srcRaw = document.getElementById("fs_src").value.trim();
  if (!font || !srcRaw) { alert(t("fill_font")); return; }
  const sources = srcRaw.split(/[,，]/).map(s => s.trim()).filter(Boolean);
  document.getElementById("fsresult").innerHTML =
    `<div class="box ok">${t("font_running")}<div class="logbox" id="fslog" ` +
    `style="margin-top:8px;max-height:140px"></div></div>`;
  document.getElementById("fsrun").disabled = true;
  /* 审核修复（中-26）：起新任务作废旧轮询链 */
  FS_GEN++;
  const gen = FS_GEN;
  const d = await api("/api/slimfont", { font, sources, charset: {
    base_latin: document.getElementById("fs_latin").checked,
    cjk_punct: document.getElementById("fs_cjk").checked,
    fullwidth: document.getElementById("fs_full").checked,
    kana: document.getElementById("fs_kana").checked,
    extra_chars: document.getElementById("fs_extra").value } });
  if (gen !== FS_GEN) return;
  if (!d.ok) {
    document.getElementById("fsresult").innerHTML =
      `<div class="box err">${esc(d.error)}</div>`;
    document.getElementById("fsrun").disabled = false;
    return;
  }
  FS_JOB = d.job; FS_IDX = 0;
  pollFontSlim(gen);
}

async function pollFontSlim(gen) {
  if (gen !== FS_GEN) return;
  const d = await api(`/api/job/${FS_JOB}?since=${FS_IDX}`);
  if (gen !== FS_GEN) return;
  if (!d.ok) {
    /* 审核修复（中-29）：任务丢失/断连时恢复按钮并给状态 */
    document.getElementById("fsrun").disabled = false;
    document.getElementById("fsresult").innerHTML =
      `<div class="box err">${esc(d.error || "")}</div>`;
    return;
  }
  const logEl = document.getElementById("fslog");
  for (const l of d.logs) {
    FS_IDX++;
    if (logEl) logEl.textContent +=
      `[${t("stages")[l.stage] || l.stage}] ${trLog(l.message)}\n`;
  }
  if (d.status === "running") { setTimeout(() => pollFontSlim(gen), 500); return; }
  document.getElementById("fsrun").disabled = false;
  loadHistory();
  if (d.status === "error") {
    document.getElementById("fsresult").innerHTML =
      `<div class="box err">${esc(d.error)}</div>`;
    return;
  }
  FS_RESULT = d.result;
  renderFontResult(d.result);
}

function renderFontResult(r) {
  let html = `<div class="box ok">${tf("font_done", { n: r.charset_size })}<br>`;
  for (const o of r.outputs) {
    const sizeTxt = o.old_size
      ? `${fmtSize(o.old_size)} → ${fmtSize(o.new_size)}` : fmtSize(o.new_size);
    const glyphTxt = o.glyphs_before != null
      ? tf("font_glyphs", { a: o.glyphs_before, b: o.glyphs_after }) : "";
    html += `· ${esc(o.src)}：${glyphTxt}${sizeTxt}` +
      `<br>&nbsp;&nbsp;${t("font_out")}：${esc(o.out)}<br>`;
  }
  html += `${t("font_charlist")}：${esc(r.charlist)}</div>`;
  const acts = [];
  for (const o of (r.outputs || []))
    if (o.out) acts.push({ kind: "file", path: o.out, label: t("locate_font") });
  if (r.charlist) acts.push({ kind: "file", path: r.charlist, label: t("locate_charlist") });
  html += pathActionButtons(acts);
  if (r.warnings && r.warnings.length)
    html += warnBox(r.warnings);
  document.getElementById("fsresult").innerHTML = html;
}

/* ===================== APK 瘦身（图形界面） ===================== */
function sigMode() {
  return document.querySelector('input[name=sigmode]:checked').value;
}
document.querySelectorAll('input[name=sigmode]').forEach(r =>
  r.addEventListener("change", () => {
    const own = sigMode() === "own";
    document.getElementById("ownkeyrow").classList.toggle("hidden", !own);
    document.getElementById("ownkeyrow2").classList.toggle("hidden", !own);
  }));

function prepareRunCard() {
  document.getElementById("runcard").classList.remove("hidden");
  document.getElementById("log").textContent = "";
  document.getElementById("resultarea").innerHTML = "";
  document.getElementById("runstatus").textContent = t("st_preparing");
  resetProgress();
  LAST_RESULT = null;
  LOGIDX = 0;
  scrollToRunCard();
}

async function runApkSlim() {
  const path = document.getElementById("apk_path").value.trim();
  if (!path) { alert(t("fill_apk")); return; }
  const sig = sigMode();
  const body = {
    path,
    preset: document.getElementById("apk_preset").value,
    remap: document.getElementById("apk_remap").checked,
    gen_key: sig === "gen",
    keystore: sig === "own" ? document.getElementById("apk_ks").value.trim() : null,
    ks_pass: sig === "own" ? document.getElementById("apk_kspass").value : null,
    key_alias: sig === "own"
      ? (document.getElementById("apk_ksalias").value.trim() || null) : null,
  };
  if (sig === "own" && (!body.keystore || !body.ks_pass)) {
    alert(t("sig_own_need")); return;
  }
  prepareRunCard();
  /* 审核修复（中-26）：起新任务作废旧轮询链 */
  JOB_GEN++;
  const gen = JOB_GEN;
  document.getElementById("apkrunbtn").disabled = true;
  const d = await api("/api/slimapk", body);
  if (gen !== JOB_GEN) return;
  if (!d.ok) {
    document.getElementById("resultarea").innerHTML =
      `<div class="box err">${esc(d.error)}</div>`;
    document.getElementById("apkrunbtn").disabled = false;
    return;
  }
  JOB = d.job;
  saveJob(d.job);
  JOB_RUNNING = true;
  /* 审核修复（中-27）：APK 瘦身引擎层无取消通道，不显示无效的
     "停止任务"按钮（不再让用户看到"正在停止…"却停不下来） */
  document.getElementById("cancelbtn").style.display = "none";
  pollJob(gen);
}

/* ===================== APK 预分析（只读扫描，不动原包） ===================== */
async function analyzeApk() {
  if (APK_ANALYZE_BUSY) return;
  const path = document.getElementById("apk_path").value.trim();
  if (!path) { alert(t("fill_apk")); return; }
  const info = document.getElementById("apkanalyzeinfo");
  info.classList.remove("hidden");
  info.innerHTML = `<div class="box ok">` +
    `<div id="apkstate" role="status" aria-live="polite">${t("apk_analyzing")}</div>` +
    `<div class="progress ind" id="apkprogress"><i id="apkprogressfill"></i></div>` +
    `<div class="logbox" id="apklog" style="margin-top:8px;max-height:110px"></div></div>`;
  const btn = document.getElementById("apkanalyze");
  btn.disabled = true;
  APK_ANALYZE_BUSY = true;

  const d = await api("/api/analyze", { path, mode: "apk" });
  if (!d || d.ok !== true) {
    APK_ANALYZE_BUSY = false;
    btn.disabled = false;
    info.innerHTML = `<div class="box err">${esc(apiErr(d))}` +
      `<br>${t("apk_analyze_err")}</div>`;
    return;
  }
  APK_ANALYZE_JOB = d.job;
  APK_ANALYZE_IDX = 0;
  APK_ANALYZE_START = Date.now();
  APK_ANALYZE_GEN++;
  pollApkAnalyze(path, APK_ANALYZE_GEN);
}

async function pollApkAnalyze(path, gen) {
  if (gen !== APK_ANALYZE_GEN) return;
  const d = await api(`/api/job/${APK_ANALYZE_JOB}?since=${APK_ANALYZE_IDX}`);
  if (gen !== APK_ANALYZE_GEN) return;
  const info = document.getElementById("apkanalyzeinfo");
  const btn = document.getElementById("apkanalyze");
  if (!d.ok) {
    APK_ANALYZE_BUSY = false;
    btn.disabled = false;
    info.innerHTML = `<div class="box err">${esc(apiErr(d))}<br>${t("apk_analyze_err")}</div>`;
    return;
  }
  const logEl = document.getElementById("apklog");
  const stateEl = document.getElementById("apkstate");
  const lines = [];
  for (const l of d.logs) {
    APK_ANALYZE_IDX++;
    lines.push(`[${t("stages")[l.stage] || l.stage}] ${trLog(l.message)}`);
    if (stateEl) stateEl.textContent = trLog(l.message);
  }
  if (logEl && lines.length) {
    logEl.textContent = (logEl.textContent + lines.join("\n") + "\n")
      .split("\n").filter(Boolean).slice(-6).join("\n");
    logEl.scrollTop = logEl.scrollHeight;
  }
  if (d.status === "running" || d.status === "queued") {
    setTimeout(() => pollApkAnalyze(path, gen), 300);
    return;
  }
  APK_ANALYZE_BUSY = false;
  btn.disabled = false;
  const prog = document.getElementById("apkprogress");
  if (prog) { prog.classList.remove("ind"); document.getElementById("apkprogressfill").style.width = "100%"; }
  if (d.status === "error") {
    info.innerHTML = `<div class="box err">${esc(d.error || "")}<br>${t("apk_analyze_err")}</div>`;
    return;
  }
  const res = d.result || {};
  if (!res.apk || !res.report) {
    /* 旧后台把 mode=apk 当成品分析，或接口尚未落地 */
    info.innerHTML = `<div class="box err">${t("api_too_old")}<br>${t("apk_analyze_err")}</div>`;
    return;
  }
  if (stateEl) stateEl.textContent = t("analyze_done");
  const wait = Math.max(0, 1000 - (Date.now() - APK_ANALYZE_START));
  setTimeout(() => {
    if (gen !== APK_ANALYZE_GEN) return;
    APK_REPORT = { report: res.report, apk: res.apk,
                   root: res.report.root || path };
    renderApkReport();
    api("/api/recent", { path }).then(r => {
      if (r && r.ok) { RECENT = r.recent; renderRecent(); }
    });
  }, wait);
}

function renderApkReport() {
  if (!APK_REPORT) return;
  const a = APK_REPORT.apk || {};
  const r = APK_REPORT.report || { entries: [], warnings: [] };
  const statsHtml =
    `<div class="stat big"><b>${fmtSize(a.apk_size)}</b><span>${t("apk_stat_size")}</span></div>` +
    `<div class="stat"><b>${a.optimizable != null ? a.optimizable : (r.entries || []).length}</b>` +
    `<span>${t("apk_stat_optimizable")}</span></div>` +
    `<div class="stat"><b>${fmtSize(a.est_saving)}</b><span>${t("apk_stat_est")}</span></div>` +
    `<div class="stat big"><b>${fmtSize(a.est_saving_remap)}</b><span>${t("apk_stat_est_remap")}</span></div>`;
  let extraWarn = "";
  if (a.signed === false) extraWarn += `<div class="box warn">${t("apk_signed_warn")}</div>`;
  if (a.has_renpy_layout === false) extraWarn += `<div class="box err">${t("apk_not_renpy")}</div>`;
  /* 后台报告的同类中文警告已用上面两段本地化文案替代，避免重复 */
  let warns = (r.warnings || []).slice();
  if (a.signed === false) warns = warns.filter(w => !w.includes("未签名"));
  if (a.has_renpy_layout === false) warns = warns.filter(w => !w.includes("x-game"));
  const report = Object.assign({}, r, { warnings: warns });
  renderReport(report, "apk", {
    card: "apkreportcard", stats: "apkreportstats", table: "apkreporttable",
    warn: "apkreportwarn", count: "apkreportcount", actions: "apkreportactions",
    statsHtml, extraWarn,
  });
  const meta = document.getElementById("apkreportmeta");
  if (meta) meta.textContent = tf("apk_stat_meta", {
    entries: a.total_entries != null ? a.total_entries : "—",
    scripts: a.script_count != null ? a.script_count : "—",
  });
}

/* ===================== 任务历史（设置页卡片） ===================== */
function historyStatus(job) {
  const states = t("hist_states") || {};
  return states[job.status] || job.status || states.unknown || "—";
}
function historyKind(k) {
  const d = t("q_kinds");
  return (d && d[k]) || k || "—";
}
function historyTime(ts) {
  if (!ts) return "";
  const dt = new Date(ts * 1000);
  if (isNaN(dt.getTime())) return "";
  const locale = ({ zh: "zh-CN", pt: "pt-BR" })[LANG] || LANG;
  try { return dt.toLocaleString(locale); } catch (e) { return dt.toLocaleString(); }
}
function jobSummary(job) {
  return (job && typeof job.summary === "object" && job.summary) || {};
}
function jobSaved(job) {
  const s = jobSummary(job);
  const cands = [s.saved_bytes, s.optimize && s.optimize.saved_bytes,
                 s.apk && s.apk.saved_bytes];
  for (const v of cands)
    if (typeof v === "number" && isFinite(v)) return v;
  return null;
}
/* 产物路径：后台摘要字段可能来自不同任务类型，挨个找第一个像路径的 */
function jobArtifact(job) {
  const s = jobSummary(job);
  const cands = [
    s.output, s.archive_output, s.artifact, s.working_dir, s.output_dir, s.keystore,
    s.package && s.package.destination,
    s.optimize && s.optimize.working_dir,
  ];
  for (const c of cands) if (typeof c === "string" && c.trim()) return c.trim();
  return "";
}
function looksLikeFile(p) {
  return isApkName(p) || isArchiveName(p) || /\.[A-Za-z0-9]{1,8}$/.test(p);
}
function historyTitle(job) {
  const s = jobSummary(job);
  return job.title || s.title || s.path || s.input || t("hist_untitled");
}

async function loadHistory() {
  const state = document.getElementById("histstate");
  const list = document.getElementById("histlist");
  if (!state || !list) return;
  if (!HISTORY) state.textContent = t("hist_loading");
  const d = await api("/api/history?limit=20");
  if (!d || d.ok !== true || !Array.isArray(d.jobs)) {
    HISTORY = [];
    const reason = apiErr(d, "");
    /* 旧后台缺接口：直接给“版本不支持”的升级提示，不再叠加“加载失败” */
    state.textContent = (!d || d.ok === undefined) ? reason
      : (reason ? t("hist_fail") + " " + reason : t("hist_fail"));
    list.innerHTML = "";
    return;
  }
  HISTORY = d.jobs;
  renderHistory();
}

function renderHistory() {
  const state = document.getElementById("histstate");
  const list = document.getElementById("histlist");
  if (!state || !list) return;
  const jobs = HISTORY || [];
  if (!jobs.length) {
    state.textContent = t("hist_empty");
    list.innerHTML = "";
    return;
  }
  state.textContent = "";
  list.innerHTML = jobs.map(job => {
    const status = String(job.status || "unknown");
    const saved = jobSaved(job);
    const artifact = jobArtifact(job);
    const err = job.error ? String(job.error).split("\n")[0] : "";
    const sub = [];
    if (saved != null) sub.push(tf("hist_saved", { s: fmtSize(saved) }));
    let acts = "";
    if (artifact) {
      acts = pathActionButtons([{
        kind: looksLikeFile(artifact) ? "file" : "dir",
        path: artifact,
        label: t("hist_locate"),
      }]);
    }
    return `<div class="histrow" role="listitem">` +
      `<div class="hist-main">` +
      `<div class="hist-head">` +
      `<span class="hist-badge st-${esc(status)}">${esc(historyStatus(job))}</span>` +
      `<span class="hist-kind">${esc(historyKind(job.kind))}</span>` +
      `<span class="hist-time">${esc(historyTime(job.finished || job.created))}</span>` +
      `</div>` +
      `<div class="hist-title">${esc(historyTitle(job))}</div>` +
      (sub.length ? `<div class="hist-sub">${sub.map(esc).join(" · ")}</div>` : "") +
      (err ? `<div class="hist-sub hist-error">${esc(err.length > 160 ? err.slice(0, 157) + "…" : err)}</div>` : "") +
      `</div>` +
      `<div class="hist-actions">${acts}</div>` +
      `</div>`;
  }).join("");
}

async function clearHistory() {
  const okay = await confirmDanger({
    title: t("hist_clear_title"),
    message: t("hist_clear_msg"),
    confirmText: t("hist_clear_ok"),
  });
  if (!okay) return;
  const d = await api("/api/history", undefined, "DELETE");
  if (!d || d.ok !== true) { toast(apiErr(d, t("hist_fail"))); return; }
  HISTORY = [];
  renderHistory();
  const state = document.getElementById("histstate");
  if (state) state.textContent = tf("hist_cleared", { n: d.cleared != null ? d.cleared : 0 });
}

/* ===================== SDK 设置 ===================== */
async function saveSdk() {
  const d = await api("/api/sdk", { sdk_path: document.getElementById("sdkpath").value.trim() });
  const msg = document.getElementById("sdkmsg");
  msg.textContent = d.ok ? t("saved") : d.error;
  msg.style.color = d.ok ? "var(--ok-ink)" : "var(--err-ink)";
  if (d.ok) loadEnv();
}

/* ===================== 危险操作确认框（可复用，Promise<boolean>） ===================== */
function confirmDanger(opts) {
  return new Promise(resolve => {
    const root = document.getElementById("modalroot");
    if (!root) { resolve(false); return; }
    MODAL.lastFocus = document.activeElement;
    MODAL.resolve = resolve;
    document.getElementById("modaltitle").textContent = (opts && opts.title) || "";
    document.getElementById("modalmsg").textContent = (opts && opts.message) || "";
    document.getElementById("modalconfirm").textContent =
      (opts && opts.confirmText) || t("confirm");
    document.getElementById("modalcancel").textContent = t("btn_cancel");
    root.classList.remove("hidden");
    root.setAttribute("aria-hidden", "false");
    /* 默认焦点放在“取消”上：危险操作不默认确认 */
    document.getElementById("modalcancel").focus();
  });
}

function closeModal(result) {
  const root = document.getElementById("modalroot");
  if (!root || root.classList.contains("hidden")) return;
  root.classList.add("hidden");
  root.setAttribute("aria-hidden", "true");
  const resolve = MODAL.resolve;
  MODAL.resolve = null;
  const last = MODAL.lastFocus;
  MODAL.lastFocus = null;
  if (resolve) resolve(!!result);
  /* 关闭后把焦点还给触发者（键盘用户不迷路） */
  if (last && typeof last.focus === "function") {
    try { last.focus(); } catch (e) {}
  }
}

document.getElementById("modalcancel").addEventListener("click", () => closeModal(false));
document.getElementById("modalconfirm").addEventListener("click", () => closeModal(true));
document.querySelector("#modalroot .modal-backdrop")
  .addEventListener("click", () => closeModal(false));
document.addEventListener("keydown", e => {
  const root = document.getElementById("modalroot");
  if (!root || root.classList.contains("hidden")) return;
  if (e.key === "Escape") { e.preventDefault(); closeModal(false); return; }
  if (e.key === "Tab") {
    /* 只有两个可聚焦按钮：把 Tab 圈在弹窗内 */
    const nodes = [document.getElementById("modalcancel"),
                   document.getElementById("modalconfirm")];
    const idx = nodes.indexOf(document.activeElement);
    if (e.shiftKey) {
      if (idx <= 0) { e.preventDefault(); nodes[1].focus(); }
    } else if (idx === -1 || idx === nodes.length - 1) {
      e.preventDefault(); nodes[0].focus();
    }
  }
});

/* ===================== 退出工具 ===================== */
async function shutdownTool() {
  if (!confirm(t("quit_confirm"))) return;
  document.getElementById("runstate").textContent = t("quit_ing");
  try {
    await api("/api/shutdown", {});
    document.getElementById("runstate").textContent = t("quit_done");
  } catch (e) {
    document.getElementById("runstate").textContent = t("quit_sent");
  }
}

/* ===================== 拖拽到图标自动填充 ===================== */
async function handleOpenParam() {
  const open = new URLSearchParams(location.search).get("open");
  if (!open) return;
  if (isApkName(open)) { go("apk"); document.getElementById("apk_path").value = open; }
  else { document.getElementById("path").value = open; if (isArchiveName(open)) go("dist"); }
  history.replaceState(null, "", location.pathname);
  if (!isApkName(open)) await analyze();   // APK 不自动开跑，由用户确认方案后手动开始
}

/* ===================== 启动 ===================== */
/* 语言选择器选项：按 LANGS 注册表动态生成（多语言免改 HTML） */
(function initLangSel() {
  const sel = document.getElementById("langsel");
  for (const [code, name] of Object.entries(LANGS)) {
    const o = document.createElement("option");
    o.value = code; o.textContent = name;
    sel.appendChild(o);
  }
  sel.value = LANG;
})();
applyModeUI();
applyLang();
applyThemeBtn();
loadEnv();
loadRecent();
handleOpenParam();
/* 任务队列：右下角面板每 2 秒看一眼后台；打开页面先接回未完成任务 */
document.getElementById("queuelist").addEventListener("click", async e => {
  const btn = e.target.closest("[data-qid]");
  if (!btn) return;
  btn.disabled = true;
  await api(`/api/job/${btn.dataset.qid}/cancel`, {});
  refreshQueue();
});
refreshQueue();
setInterval(refreshQueue, 2000);
tryResumeJob();
