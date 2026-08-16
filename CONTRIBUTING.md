# 贡献指南 / Contributing

欢迎提交 issue 和 PR！本项目按 **AGPL-3.0** 发布，提交贡献即表示
同意你的贡献以同一协议并入。

## 开发环境 / Setup

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt pyinstaller pytest
python main.py            # 启动图形界面（开发模式）
```

外部依赖（可选，按功能需要）：Ren'Py SDK、FFmpeg、Java/JDK，
详见 README「环境要求」。

## 动手之前 / Before you code

1. 先读 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)——里面有 9 条
   **安全红线**（工作副本、没变小不替换、引用门控、引擎目录保护等），
   任何改动不得违反。
2. 新需求先落进 [docs/BACKLOG.md](docs/BACKLOG.md) 再动代码。
3. 界面改动遵守 [web/static/index.html](web/static/index.html) 的
   单文件零依赖架构：不引外部 CSS/JS、不加载网络资源；新增文案必须
   四语齐全（I18N 的 zh/en/ru/es 字典，缺一会被测试拦下）。

## 提交要求 / PR checklist

- [ ] `pytest tests -q` 全绿（当前基线 77 项），新功能须补回归测试
- [ ] 涉及文件读写的新代码：路径来自压缩包/封包条目时必须过 `utils.safe_join`
- [ ] 破坏性操作：先备份/副本、可反悔、产出修改清单
- [ ] 界面改动：中/英 × 亮/暗四组合自查
- [ ] 提交信息风格参照现有历史（`feat(模块): 摘要; N tests green`）

## 翻译指南 / Translation guide

想添加新语言（比如 pt/ja），四步：

1. **界面字典**：在 [web/static/index.html](web/static/index.html) 的 `I18N`
   里新增一本字典（照 zh 的键集逐键翻译；`tests/test_i18n.py` 会强制
   四本字典键集一致，缺键立刻红）。
2. **登记语言**：同文件的 `LANGS` 注册表加一行（语言选择器选项由它生成），
   并把新语言码加进 `tests/test_i18n.py` 的 `LANGS` 常量。
3. **日志模板**：`LOG_PATTERNS` 里每条模式补该语言的模板
   （缺省会回退英文，不影响运行）。
4. **文档**：新增 `README.<语言码>.md`（参照 README.en.md 结构），并把
   各 README 顶部的语言行与 README.md 的「多语言支持」表格补上新条目。

## 版本与发布 / Versioning

版本号事实源在 `rtools/__init__.py` 的 `__version__`，经 `/api/env`
与 `cli --version` 暴露；发 Release 时同步更新 docs/STATUS.md。
