## 这个 PR 做什么 / What this PR does

<!-- 一两句话说清目的，关联 issue 号 -->

## 自查清单 / Checklist

- [ ] `pytest tests -q` 全绿，新行为有回归测试
- [ ] 未违反 docs/ARCHITECTURE.md 的安全红线（副本/备份/引用门控/引擎保护）
- [ ] 界面改动已自查 中/英 × 亮/暗 四组合，新文案中英齐全
- [ ] 未引入新的外部依赖；若必须引入或抬下限，已按 docs/CONTRIBUTING.md「依赖更新」
  四步走完：同步 `requirements.txt` / `requirements-dev.txt`、重生成
  `requirements-lock.txt`（该文件头部口径，不手改）、`tests/test_deps_lock.py` 绿、
  并更新 THIRD_PARTY_NOTICES.md（含协议兼容性说明）
- [ ] 提交信息符合仓库风格

## 协议确认 / License

本人同意本贡献按 AGPL-3.0 并入本项目。
