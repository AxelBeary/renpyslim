# RenPySlim 交接状态（截至 2026-08-15 Cadaver 全量实测日）

> 给下一次开工的自己/协作者：读完本页 + BACKLOG.md + ARCHITECTURE.md 即可无缝接手。

## 当前版本：v0.10.0（界面大改版；v0.9.0 曾发布 GitHub Release，附 exe）

仓库：https://github.com/AxelBeary/renpyslim （公开，AGPL-3.0；v0.10 起由 Apache-2.0 改签，
用户拍板；第三方声明见 THIRD_PARTY_NOTICES.md）
Release：https://github.com/AxelBeary/renpyslim/releases/tag/v0.10.0（自更新检查靶子，
附 v0.10.0 exe；已验证 updater 正确识别）
回归测试：68 项全绿（`pytest tests -q`，含审核修复回归 12 条）

## Cadaver 样本实测战绩（用户提供的真实游戏，全部实测通过）

| 样本 | 结果 |
|---|---|
| 解包成品（带 rpy 源码，含 TTC/可变字体） | 省 303.9MB，88 处引用改写，启动正常 |
| 【苹果Mac】zip 直进直出 | 省 303.9MB，交付瘦身 zip（顺手修复 Mac .app 深层 game 定位 bug） |
| 【安卓】密码 zip（密码 2510）内 APK | 同名版 554→427MB；--remap 版见下行 |
| 【安卓】无密码单独 APK（581MB） | **--remap 全转换：581→148.7MB（-74.4%）**，381 个资源换格式（图→WebP、音→OGG），重映射脚本注入，签名验证通过 |
| 【电脑】zip | 与桌面版已测包字节相同，结果沿用（省 216MB） |

产物留存（gitignore 中）：
- `_cadaver_work\apk2\JigsawPuzzles-v14.1-slim-remap-signed.apk`（148.7MB，最新战果）
- `_cadaver_work\apk_out\JigsawPuzzles-v14.1-slim-signed.apk`（279MB，仅同名压缩版）
- `_cadaver_work\apk_out\renpyslim.keystore` + `renpyslim-钥匙备忘.txt`（签名钥匙+密码，务必保管）

## APK 瘦身能力（F1，CLI `slimapk`）

- 同名压缩：只压 assets/x-game/ 下的图/音/字体，引擎目录（x-renpy）绝不碰
- `--remap`（实验性，收益最大）：图转 WebP、音转 OGG，用 SDK 现场编译重映射
  脚本（rpyc）注入 APK，运行时透明换文件，不改任何引用；编译失败自动放弃转换保原样
- 签名三姿势：① `--keystore + --ks-pass` 用原钥匙（可覆盖更新）
  ② 同上传自有钥匙 ③ `--gen-key` 现场造新钥匙+密码备忘（新身份，玩家需卸载重装）
- 签名走纯英文临时路径（防 apksigner 对乱码路径报 Bad pathname）
- **待真机验收**：音频走重映射是新路径，装手机听背景乐/音效是否正常

## 其余已完成（详见 BACKLOG.md）

- B1~B9 借鉴清单全部完成并实测；F2 自更新、F3 崩溃转储、F4 取消按钮、F6 字节级进度
- 成品线优化已并行化，技术债登记表清零

## 下次开工可选方向（需用户点头）

1. APK 瘦身上图形界面（目前仅 CLI）
2. 音频重映射真机验收结果跟进（用户装机测试中）
3. F5 lint 自修、F7 语法资产（远期）
4. 用户真实游戏成品瘦身（等用户提供样本）

## 2026-08-15 前端整体重构（已完成，用户验收通过，版本升 v0.10.0）

用户拍板：完全重构界面，要实用美观、中英双语、亮暗双主题。
方向：**侧边栏导航 + 暖色圆润风格**（浅色暖白底，暗色为中性暖灰
——第一版暖棕暗色被用户否掉后换的，别再改回棕色）。

已交付（用户终验通过）：
- 正式版 `web/static/index.html` 全量重写：零依赖单文件，侧边栏+顶栏，
  中英字典即时切换（localStorage 记忆），亮暗双主题（跟随系统+手动，
  首屏防闪），进度条可视化，选项分组折叠，结果统计卡片化
- 品牌位用用户提供的真图标（web/static/logo.png，来自 assets/icon_256.png）
- 左下角：运行状态 + 版本号 + GitHub 链接 + 退出按钮；窄窗口时退出
  自动变电源图标按钮（用户拍板的位置，别移去顶栏）
- 后端 API 契约零改动；pytest 68 项全绿；exe 已重建
- 真实后端 E2E 实测通过：分析 515 资源/250.6MB，执行省 152.5MB，
  lint 通过，刷新记忆语言/主题正常
- 版本号 0.9.0 → 0.10.0（rtools/__init__.py 事实源）

已知边界：后端返回的日志/警告保持中文原文（计划内，英文模式下
日志区为中文）。v0.10.0 GitHub Release 已发布（附 exe，用户拍板推送）。

## 2026-08-15 全面代码审核结果（17 项：15 属实已修，1 半属实已修，1 不属实）

审核当日逐条对照代码核验，属实项当日全部修复，
新增 12 条回归测试锁住（tests/test_bugfix_audit.py，共 68 项全绿）。

严重级（4/4 属实，已修）：
1. ✅ run_dist in_place 删玩家存档且备份漏存档 → in_place 跳过垃圾清理
   （对齐工程模式），make_backup_zip 改用 _BACKUP_SKIP 不再排除 saves
2. ✅ APK 字符集提取链路断裂（rpyc 属 OTHER 从不解出）→ 提取阶段一并
   解出 x-game 内脚本/文本，按类型分别解码；真实 APK 冒烟提到 2193 汉字
3. ✅ 脚本封 rpa 时成品字符集扫空 → scan_rpa_assets 新增 extract_scripts，
   run_dist 已开启
4. ✅ BASE_CJK_PUNCT 弯引号被 ASCII 引号截断 → 改用 \u 转义写入并加注释防复发

中等级（7/7 属实，已修）：remap 二次运行先读回旧映射再合并（remap.py
新增 parse_remap_mapping）；隔离区改按 game/ 基准拼路径；run_dist_smart
目录分支补传 cancel；新增 find_suffix_clashes 预检同名撞车（工程/成品/APK
三处撞车项降级同名压缩）；utils.safe_join 路径净化挡住 zip-slip（scanner
与 apk 解包均已接入，盘符/.. 一律拒绝）；RpaWriter 新增 abort，重建异常
句柄必关；取消时 _flush_partial_changelog 落 cancelled=true 的部分清单。

轻微级（3 属实已修，1 半属实已修，1 不属实）：
- ✅ PC/Mac 打包补 1 小时超时（对齐安卓分支）
- ✅ slim_apk ZipFile 句柄移入 finally（异常不再锁死原 APK）
- ✅ cache 并发写 tmp 名加随机后缀
- ✅（半属实）ffmpeg 探测补容器级 bit_rate 回退（WAV 等流级无码率）
- ❌（不属实）read_rpyc_text 槽位循环实际受文件长度限界，无需修

## 本机环境备忘

- Ren'Py SDK 8.5.3：E:\renpy（打包、rpyc 编译都委托它）
- 安卓工具链：JDK 21 ✅（keytool）、rapt\Sdk\build-tools\35.0.0（apksigner/zipalign）✅
- 测试钥匙两把：E:\renpy\JIGSAW_PUZZLES\（用户旧的）+ _cadaver_work\apk_out\renpyslim.keystore（工具生成的，密码在备忘文件里）
- FFmpeg：winget 全局版
- 测试工程：E:\renpy\JIGSAW_PUZZLES（用户授权随便折腾）
- Cadaver 样本：仓库内 Cadaver\ 目录（用户提供的测试素材，gitignore 不入仓）

## 开工前例行检查

```
git status                                  # 应干净
.venv\Scripts\python -m pytest tests -q     # 应 68 passed
dist\RenPySlim.exe                          # 如代码有变，先 build_exe.bat 重建
```

## 已知怪癖（别误判）

- exe 首次启动慢（30s+）是杀软扫描，不是卡死
- PowerShell 管道会转码中文 JSON：验证 CLI 输出要用子进程直读，别用 `|` 管道接 python 解析
- 沙箱偶发对长组合命令报"拒绝访问"：拆成短命令单独跑即可
- 中文文件名传命令行会乱码：用 Python glob 拿路径，别在 shell 里拼中文路径
- git add -A 前先看 .gitignore 是否挡住测试样本（曾误将 3GB 样本入提交，已回退教训）
- apksigner（Java）对乱码输出路径报 Bad pathname：代码里已用英文临时路径规避
