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
   同时提供中英两份（I18N 字典）。

## 提交要求 / PR checklist

- [ ] `pytest tests -q` 全绿（当前基线 74 项），新功能须补回归测试
- [ ] 涉及文件读写的新代码：路径来自压缩包/封包条目时必须过 `utils.safe_join`
- [ ] 破坏性操作：先备份/副本、可反悔、产出修改清单
- [ ] 界面改动：中/英 × 亮/暗四组合自查
- [ ] 提交信息风格参照现有历史（`feat(模块): 摘要; N tests green`）

## 版本与发布 / Versioning

版本号事实源在 `rtools/__init__.py` 的 `__version__`，经 `/api/env`
与 `cli --version` 暴露；发 Release 时同步更新 docs/STATUS.md。
