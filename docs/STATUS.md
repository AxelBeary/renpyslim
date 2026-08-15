# RenPySlim 交接状态（截至 2026-08-15 Cadaver 全量实测日）

> 给下一次开工的自己/协作者：读完本页 + BACKLOG.md + ARCHITECTURE.md 即可无缝接手。

## 当前版本：v0.9.0（已发布 GitHub Release，附 exe）

仓库：https://github.com/AxelBeary/renpyslim （公开，Apache-2.0）
Release：https://github.com/AxelBeary/renpyslim/releases/tag/v0.9.0（自更新检查的靶子）
回归测试：56 项全绿（`pytest tests -q`）

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
.venv\Scripts\python -m pytest tests -q     # 应 56 passed
dist\RenPySlim.exe                          # 如代码有变，先 build_exe.bat 重建
```

## 已知怪癖（别误判）

- exe 首次启动慢（30s+）是杀软扫描，不是卡死
- PowerShell 管道会转码中文 JSON：验证 CLI 输出要用子进程直读，别用 `|` 管道接 python 解析
- 沙箱偶发对长组合命令报"拒绝访问"：拆成短命令单独跑即可
- 中文文件名传命令行会乱码：用 Python glob 拿路径，别在 shell 里拼中文路径
- git add -A 前先看 .gitignore 是否挡住测试样本（曾误将 3GB 样本入提交，已回退教训）
- apksigner（Java）对乱码输出路径报 Bad pathname：代码里已用英文临时路径规避
