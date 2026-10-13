# 贡献指南 / Contributing

欢迎提交 issue 和 PR！本项目按 **AGPL-3.0** 发布，提交贡献即表示
同意你的贡献以同一协议并入。

## 开发环境 / Setup

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt pyinstaller
python main.py            # 启动图形界面（开发模式）
```

源码运行需 **Python 3.11+**（由依赖下限决定；发布的 exe 自带解释器）。

外部依赖（可选，按功能需要）：Ren'Py SDK、FFmpeg、Java/JDK，
详见 README「环境要求」。

## 动手之前 / Before you code

1. 先读 [docs/ARCHITECTURE.md](ARCHITECTURE.md)——里面有 9 条
   **安全红线**（工作副本、没变小不替换、引用门控、引擎目录保护等），
   任何改动不得违反。
2. 新需求先落进 [docs/BACKLOG.md](BACKLOG.md) 再动代码。
3. 界面改动遵守 [web/static/index.html](../web/static/index.html) 的
   单文件零依赖架构：不引外部 CSS/JS、不加载网络资源；新增文案必须
   八语齐全（I18N 的 zh/en/ru/es/pt/tr/de/fr 字典，缺一会被测试拦下）。

## 依赖更新 / Dependency updates

口径（2026-10-13 起）：**下限 = 首个无已知 CVE 且实测通过的版本**。不再追求“越宽容越好”：
`>=` 下限写得太旧，等于允许旧 Python 环境装上有洞的版本，而这类洞
Dependabot 看不见（它只评估实际解析出的版本）。分工：

- `requirements.txt` / `requirements-dev.txt`：人工抬下限（安全告警与发版周期驱动）
- `requirements-lock.txt`：逐条精确 pin（含传递依赖与 PyInstaller），`release.yml`
  以 `-c` 消费它，保证 exe 构建可复现；`ci.yml` 故意不锁，装最新版当预警
- `.github/dependabot.yml`：pip 生态 `open-pull-requests-limit: 0`，版本 bump PR 归零，
  安全更新 PR 不受此限照推（实测 `ignore` + `update-types` 压不住，别再加回去）

改依赖后四步（缺一不算改完）：

1. 升到目标版本并跑门禁（三条逐字同 ci.yml）：`python -m pytest tests -q` +
   `ruff check --select F,E9 --exclude rtools/vendor rtools web cli.py main.py tests`
   + `python -m pip check`
2. 重生成 lock：`python -m pip freeze` 覆盖 body，**保留文件头部的 `#` 注释行**
   （具体命令见 requirements-lock.txt 头部；别用 `-Encoding ascii`，中文注释会被压成问号）
3. 复跑 `python -m pytest tests -q`，确认 `tests/test_deps_lock.py` 四道守卫绿
4. 同步 `docs/THIRD_PARTY_NOTICES.md` 的对应表（运行时依赖表填“使用版本”列；
   构建/测试工具表只有协议与用途两栏，不填版本）与 `docs/STATUS.md`

## 提交要求 / PR checklist

- [ ] `python -m pytest tests -q` 全绿（测试数量以收集结果为准，不在文档写死），新功能须补回归测试
- [ ] 动过依赖（requirements*.txt）：按上面「依赖更新」四步走完，lock 与清单同步提交
- [ ] 涉及文件读写的新代码：路径来自压缩包/封包条目时必须过 `utils.safe_join`
- [ ] 破坏性操作：先备份/副本、可反悔、产出修改清单
- [ ] 界面改动：中/英 × 亮/暗四组合自查
- [ ] 提交信息风格参照现有历史（`feat(模块): 摘要; N tests green`）

## 翻译指南 / Translation guide

想添加新语言（比如 ja/ko），四步：

1. **界面字典**：在 [web/static/index.html](../web/static/index.html) 的 `I18N`
   里新增一本字典（照 zh 的键集逐键翻译；`tests/test_i18n.py` 会强制
   八本字典键集一致，缺键立刻红）。
2. **登记语言**：同文件的 `LANGS` 注册表加一行（语言选择器选项由它生成），
   并把新语言码加进 `tests/test_i18n.py` 的 `LANGS` 常量。
3. **日志模板**：`LOG_PATTERNS` 里每条模式补该语言的模板
   （缺省会回退英文，不影响运行）。
4. **文档**：新增 `docs/README.<语言码>.md`（参照 docs/README.en.md 结构），并把
   各 README 顶部的语言行与 README.md 的「多语言支持」表格补上新条目。

## 版本与发布 / Versioning

版本号事实源在 `rtools/__init__.py` 的 `__version__`，经 `/api/env`
与 `cli --version` 暴露；发 Release 时同步更新 docs/STATUS.md。
