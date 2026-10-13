## 这个 PR 做什么 / What this PR does

<!-- 一两句话说清目的，关联 issue 号 -->

## 自查清单 / Checklist

- [ ] `pytest tests -q` 全绿，新行为有回归测试
- [ ] 未违反 docs/ARCHITECTURE.md 的安全红线（副本/备份/引用门控/引擎保护）
- [ ] 界面改动已自查 中/英 × 亮/暗 四组合；新文案八语齐全（zh/en/ru/es/pt/tr/de/fr，缺键会被 test_i18n 拦下）
- [ ] 未引入新的外部依赖；若必须引入或抬下限，已按 docs/CONTRIBUTING.md「依赖更新」
  四步走完（含三条门禁 pytest / ruff / `python -m pip check`、重生成
  `requirements-lock.txt`、`tests/test_deps_lock.py` 绿、同步 THIRD_PARTY_NOTICES 与 STATUS）
- [ ] 提交信息符合仓库风格

## 协议确认 / License

本人同意本贡献按 AGPL-3.0 并入本项目。
