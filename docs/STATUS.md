# RenPySlim 交接状态（截至 2026-08-15）

> 给下一次开工的自己/协作者：读完本页 + BACKLOG.md + ARCHITECTURE.md 即可无缝接手。

## 当前版本：v0.9.0（已发布到 GitHub）

仓库：https://github.com/AxelBeary/renpyslim （公开，Apache-2.0）
代码全部提交推送，工作区干净（临时产物已被 .gitignore 覆盖）。

## 已完成并验证（全部有实测证据）

| 项 | 状态 | 关键证据 |
|---|---|---|
| 分析/优化/打包三条主流程 | ✅ | the_question + JIGSAW_PUZZLES 双项目实测 |
| 成品瘦身（zip 直进直出、RPA 拆建） | ✅ | 拼图谜题 514MB zip 实测省 216MB，游戏启动正常 |
| 安卓打包 | ✅ | JIGSAW_PUZZLES 实打 release APK 271MB（钥匙有效、SDK 已装） |
| B1~B9 借鉴清单 | ✅ 全部完成 | 详见 BACKLOG.md，B9 已真机验收（16 图透明转 WebP 省 160MB） |
| 全流程大实测 | ✅ | JIGSAW v1.5.5：263.6MB → 140.8MB（-46.6%），lint 通过 |
| 回归测试 | 44 项全绿 | `pytest tests -q` |

## 本机环境备忘

- Ren'Py SDK 8.5.3：E:\renpy（打包委托它）
- 安卓：JDK 21 ✅、rapt\Sdk ✅、钥匙在 E:\renpy\JIGSAW_PUZZLES\（测试用，非真实项目密钥）
- FFmpeg：winget 装的全局版
- 测试工程：E:\renpy\JIGSAW_PUZZLES（用户授权随便折腾，非真实使用）
- 优化后成品包留存：本目录 _full_dist\（PC 140.8MB / Mac 135.3MB，gitignore 中）

## 下次开工可选方向（按用户兴趣排序，均需用户点头）

1. 拿用户真实游戏成品跑一遍成品瘦身（用户说过会给真实样本，尚未给）
2. 远期 F1：APK 瘦身——现成靶子：_apk_jigsaw\ 里那个 271MB 测试 APK
3. 远期 F2~F7：自更新、崩溃转储、取消按钮、lint 自修、字节级进度、语法资产
4. 残余债：成品线（run_dist）优化循环仍串行，触发条件见 BACKLOG 第四节

## 开工前例行检查

```
git status                          # 应干净
.venv\Scripts\python -m pytest tests -q    # 应 44 passed
dist\RenPySlim.exe                  # 如代码有变，先 build_exe.bat 重建
```

## 已知怪癖（别误判）

- exe 首次启动慢（30s+）是杀软扫描，不是卡死
- PowerShell 管道会转码中文 JSON：验证 CLI 输出要用子进程直读，别用 `|` 管道接 python 解析
- 沙箱偶发对长组合命令报"拒绝访问"：拆成短命令单独跑即可
