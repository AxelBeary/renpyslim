# AGENTS.md — AI 助手 / 自动化脚本使用指引

> 本文件写给 AI 编码助手与自动化脚本：如何用无头模式正确调用 RenPySlim。
> 人类用户请看 [README.md](README.md)（图形界面为主）。
> English version is at the bottom of this file.

## 关键事实（先读这个）

- RenPySlim 是 Windows 专属工具（Ren'Py 游戏资源瘦身 + 打包），**无 Linux/macOS 支持**。
- 有两个入口：图形界面（本地网页 127.0.0.1）和无头 CLI。**Agent 一律用无头 CLI，不要操作图形界面。**
- 无头入口：仓库根目录的 `cli.py`（Python 源码方式运行）。
- 输出约定：**结果 JSON 走 stdout，过程日志走 stderr**——解析时只读 stdout，stderr 不要当 JSON 解析。
- 所有命令的结果 JSON 顶层有 `"ok": true/false`；失败时带 `"error"` 字段。

## 运行方式

```
pip install -r requirements.txt          # 先装依赖
python cli.py <命令> <参数>
```

外部依赖（可选，缺了相关功能自动跳过）：Ren'Py SDK（打包/签名）、
FFmpeg（音频/视频）、Java JDK（安卓）。先跑一次体检确认环境：

```
python cli.py env [--sdk SDK路径]
```

## 命令速查

| 命令 | 用途 | 示例 |
|---|---|---|
| `env` | 环境体检 | `python cli.py env` |
| `analyze` | 只读分析，出报告 | `python cli.py analyze <路径> --mode project` |
| `optimize` | 瘦身优化 | `python cli.py optimize <路径> --preset balanced` |
| `full` | 优化 + SDK 打包一条龙 | `python cli.py full <工程路径> --platforms pc,mac` |
| `package` | 只打包（需 SDK） | `python cli.py package <工程路径> --platforms pc` |
| `slimapk` | APK 瘦身 + 重签名 | `python cli.py slimapk <apk> --remap --gen-key` |
| `slimfont` | 独立字体瘦身 | `python cli.py slimfont <字体> <文本来源...>` |

要点：

- `--mode` 可省略：有 `game/*.rpy` 源码自动判 `project`，否则 `dist`（成品）。
- 直接输入 zip/7z/RAR 压缩包也可以（成品瘦身直进直出）；有密码就加 `--password`。
- `--preset` 三档：`conservative`（无损优先）/ `balanced`（默认，推荐）/ `aggressive`（体积优先）。
- 产物默认写到输入路径旁边的 `_rtools_work/`（中间产物）和 `_rtools_output/`
  （报告 analysis.json、改动清单 changelog.json、校验 validation.txt、成品包）。
  可用 `--work-root` / `--output` 改位置。

## Agent 必须遵守的安全规则

1. **默认不动原件**：优化在自动复制的工作副本上进行。除非用户明确要求，
   不要加 `--in-place`（该选项会自动备份，但仍是高危操作，用前必须征得用户同意）。
2. **先 analyze 后 optimize**：先给用户看分析报告，再执行修改类命令。
3. 引擎目录 `renpy/`、`lib/`、`assets/x-renpy/` 工具会自动保护，**agent 自己也不要碰**。
4. `--delete-unreferenced` / `--quarantine-unused` 是移动疑似无用文件，默认不开；
   开启前必须征得用户同意（Ren'Py 按文件名自动加载图片，查不到引用不等于没用）。
5. `slimapk --gen-key` 会换新签名身份（玩家需卸载重装），提示用户后果后再用。
6. 不要把本地网页服务（127.0.0.1）以任何方式暴露到网络。

## 常见报错排查

| 现象 | 处理 |
|---|---|
| `找不到 Ren'Py SDK` | 用 `--sdk` 指定 SDK 目录，或先 `env` 体检 |
| 压缩包解不开 | 确认是否需要 `--password`；RAR 需要系统装有 unrar 类工具时看报错提示 |
| 音频没变小 | 检查 FFmpeg 是否在 PATH（`env` 可见） |
| JSON 里中文乱码 | 是终端编码问题，不是工具问题；stdout 本身是 UTF-8 |

---

# AGENTS.md — Guide for AI assistants / automation (English)

> This file is for AI coding agents and scripts: how to drive RenPySlim
> headlessly and correctly. Human users should read README.md (GUI-focused).

## Key facts (read first)

- RenPySlim is a **Windows-only** tool (Ren'Py game asset slimming + packaging).
  No Linux/macOS support.
- Two entry points exist: a local web GUI and a headless CLI.
  **Agents must use the headless CLI only; never drive the GUI.**
- Headless entry: `cli.py` at the repository root (run from source).
- Output contract: **result JSON on stdout, progress logs on stderr**.
  Parse stdout only; stderr is never JSON.
- Every result JSON has top-level `"ok": true/false`; failures carry `"error"`.

## Running

```
pip install -r requirements.txt
python cli.py <command> <args>
```

Optional external tools (features degrade gracefully without them):
Ren'Py SDK (packaging/signing), FFmpeg (audio/video), Java JDK (Android).
Check the environment first:

```
python cli.py env [--sdk PATH_TO_SDK]
```

## Command reference

| Command | Purpose | Example |
|---|---|---|
| `env` | Environment check | `python cli.py env` |
| `analyze` | Read-only analysis report | `python cli.py analyze <path> --mode project` |
| `optimize` | Slim/optimize assets | `python cli.py optimize <path> --preset balanced` |
| `full` | Optimize + SDK packaging | `python cli.py full <project> --platforms pc,mac` |
| `package` | Package only (needs SDK) | `python cli.py package <project> --platforms pc` |
| `slimapk` | APK slim + re-sign | `python cli.py slimapk <apk> --remap --gen-key` |
| `slimfont` | Standalone font subsetting | `python cli.py slimfont <font> <text sources...>` |

Notes:

- `--mode` is optional: auto-detected as `project` when `game/*.rpy` sources
  exist, otherwise `dist` (built game).
- zip/7z/RAR archives are accepted directly; add `--password` if protected.
- Presets: `conservative` / `balanced` (default) / `aggressive`.
- Outputs go to `_rtools_work/` (intermediate) and `_rtools_output/`
  (analysis.json, changelog.json, validation.txt, final packages) next to the
  input; override with `--work-root` / `--output`.

## Safety rules for agents

1. **Never mutate originals by default**: optimization runs on an automatic
   working copy. Do not pass `--in-place` without explicit user consent
   (it auto-backs-up, but remains high-risk).
2. **Analyze before optimizing**: show the user the report first.
3. Engine dirs (`renpy/`, `lib/`, `assets/x-renpy/`) are auto-protected —
   do not touch them yourself either.
4. `--delete-unreferenced` / `--quarantine-unused` move possibly-unused files
   and are off by default; get user consent first (Ren'Py auto-loads images
   by filename, so "no reference found" does not mean unused).
5. `slimapk --gen-key` creates a new signing identity (players must reinstall);
   warn the user before using it.
6. Never expose the local web service (127.0.0.1) to any network.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `找不到 Ren'Py SDK` | Pass `--sdk <dir>` or run `env` first |
| Archive won't extract | Check whether `--password` is needed |
| Audio unchanged | Ensure FFmpeg is on PATH (visible in `env`) |
| Garbled Chinese in JSON | Terminal encoding issue only; stdout is UTF-8 |
