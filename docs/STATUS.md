# RenPySlim 交接状态（截至 2026-08-15 Cadaver 全量实测日）

> 给下一次开工的自己/协作者：读完本页 + BACKLOG.md + ARCHITECTURE.md 即可无缝接手。

## 当前版本：v0.13.0（网页任务队列 + 断线重连 + 右下角队列面板；v0.10.0 起 AGPL-3.0）

仓库：https://github.com/AxelBeary/renpyslim （公开，AGPL-3.0；v0.10 起由 Apache-2.0 改签，
用户拍板；第三方声明见 THIRD_PARTY_NOTICES.md）
Release：https://github.com/AxelBeary/renpyslim/releases/tag/v0.11.0（自更新检查靶子，
附 v0.11.0 exe；release.yml 首次挂过一次：sanity 测试缺 pytest/httpx，已修）
回归测试：114 项全绿（`pytest tests -q`，含 2026-08-17 任务队列回归 5 条 + 审核修复回归 22 条 + 视频/反编译回归 + 早期审核回归 12 条）

## 2026-08-17 v0.13.0 发版（用户拍板：多开不冲突 + 关页不丢任务）

起因：用户提问"压缩到一半关掉网页怎么办？同时开几个网页同时转换会怎样？"
查实两个真实缺陷（关页后进度找不回；并发任务固定名产物互相覆盖），
用户拍板用"队列列表 + 右下角提醒"方案一并解决：

1. **后台任务队列**（web/app.py）：重任务（瘦身/打包/APK/字体）同一时刻
   只跑一个，后提交的自动排队（queued 状态），前一个结束自动接着跑；
   前一个崩溃/取消也不卡队列。只读分析不排队。排队中取消=秒退队。
2. **断线重连**：任务编号存 localStorage（rps-job），重新打开页面自动
   接回进度卡（优先接本页提交的，其次接任意在跑/排队的）；新增
   /api/jobs 队列总览接口，/api/job/{id} 返回 kind 字段。
3. **右下角队列面板**：有执行中/排队中任务自动出现，列出任务类型与
   状态，每条带停止键；每 2 秒刷新，多标签页都看得到，兼顾"提醒"。
4. AGENTS.md 同步：默认档位更正为 conservative（画质优先），补全
   五个实验性开关说明（--png-quant/--videos/--av1/--remap/--decompile）。
- 测试：tests/test_job_queue.py 5 条（立即开跑/排队接续/退队跳过/
  崩溃接续/接口行为）；真机冒烟：双任务提交→第二个正确排队→退队成功
- 四语言文案（zh/en/ru/es）：st_queued/st_resumed/q_title/q_kinds/q_states

## 2026-08-17 v0.12.0 发版（用户拍板：尽可能压缩，精益求精）

本版本三大主题（细节见下方各批次小节）：
1. **AUDIT-2026-08-17 审查报告 40 项缺陷全量修复**（严重 2/高 5/中 33，
   逐条核实属实后修复，新增 22 条回归测试）
2. **性能与默认策略**：多核放开（并发上限 16 路 + 视频多线程编码）；
   默认档位改为画质优先（q95 近无损）；小文件体积门槛降到 1KB
3. **压缩能力增强**：视频同编码安全重编 + AV1 实验选项（官方支持
   且更省）；unrpyc 反编译解锁无源码成品的格式转换，转换后按原样
   包回 RPA 封包（--decompile，实验性）
- 第三方署名：unrpyc（MIT）内嵌于 rtools/vendor/unrpyc/，
  THIRD_PARTY_NOTICES 已增节；exe 打包配方（build_exe.bat/两个 spec/
  release.yml）均已把 vendor 数据纳入
- Cadaver/CSE 双样本实测：电脑成品省 303.8MB（中文名 zip 全链路正确）；
  CSE 开 --decompile --videos 省 635.7MB（封包内 29 张图包回 webp）
- 待办：无（审核报告"待确认 8 项"需真实样本实测，留待有样本时处理）

## 2026-08-17 全面审查报告修复批次（AUDIT-2026-08-17.md，用户拍板执行）

- 报告结论：严重 2 + 高 5 + 中 33 项，逐条对照源码核实全部属实后按报告
  优先级修复；最小必要改动，报告标注的有意设计（如 convert_png_webp
  在模式 B 跟随档位）未动。回归测试新增 tests/test_audit_20260817.py 22 条
- 严重-1：成品模式 do_images/do_audio/do_fonts 开关失效（字体照剃）→
  dist_jobs 构建循环按 kind 过滤；严重-2：zip 中文文件名 GBK 乱码 →
  未置 UTF-8 标志条目 cp437 还原原始字节再 utf-8/gb18030 回解（端到端冒烟验证）
- 高-1：verifier 全分支补齐 suspects 键 + pipeline 改 get；高-2：Web 任务
  超时清理跳过运行中任务 + wrapper 改判空；高-3：撞名预检并入现存资源集 +
  转换前目标存在性检查 + 优化器 tmp 名随机化；高-4：find_dist_roots 返回
  全部候选，多成品包明确报错要求拆包；高-5：run_project/run_dist 改动段落
  套 BaseException 兜底落部分清单 + dist 隔离循环补存在性防御
- 中级修复（按报告编号）：中-1 取消时聚合本批已完成改动（PipelineCancelled
  携带 partial_results）；中-2 吞异常留日志 + dist 音频 FFmpeg 预检；中-3
  取消时 kill 子进程树（procutil 登记句柄/taskkill /T）+ CLI SIGINT 映射取消；
  中-4 缓存复制原子化；中-5 APK 转发产物挪到输出目录；中-6 回包后删解压目录；
  中-7 _rtools_extract 改 finally 清理 + dist_jobs 排除；中-8 垃圾目录只删
  已知安全位置；中-9 备份 zip 原子写；中-10 产物自映射防 in_place 重编码
  代际累积；中-11 被拒 APK 条目移出 targets；中-12/13 RPA 脏索引转 RpaError +
  长度校验；中-14 apksigner 改 java -jar 直调（.bat 回退时拒危险密码）；
  中-15 zipalign/apksigner 超时捕获降级；中-16 APK 重打包复用已解出文件；
  中-17 run_quiet 统一 stdin=DEVNULL；中-18 中文密码 GBK 重试 + AES 明报
  不支持；中-19 封包内音频也探码率；中-20 .ogv 改 libtheora；中-21 lint
  解码回退 cp936 + 错误行改认位置前缀；中-22 引用改写补右守卫；中-23 缓存
  2GB 上限按 mtime 淘汰；中-24 字体句柄 try/finally；中-25 CLI/Web 分析补
  extract_scripts；中-26 前端轮询代际 token + 分析防连击 + 切页保留运行卡片；
  中-27 APK 任务不显示无效停止按钮；中-28 选择框模块锁串行化；中-29 api()
  捕网络异常 + 轮询失败恢复按钮；中-30 esc() 补引号转义；中-31 压缩包分析
  强制 dist；中-32 CLI 各命令顶层兜底 + full 打包段补 try + analyze 解压失败
  清理临时目录；中-33 撞名预检并入封包内资源名
- 待确认 8 项（报告第五节）未动：需真实样本/环境实测，留待下批

## 2026-08-17 多核放开（用户拍板，28 核机器实测）

- 并行度不再写死 6 路：_worker_count 改为核心数减 2、上限 16（小批量
  仍串行）；视频编码线程放开为核心数一半（上限 16）；音频保持单
  线程编码（libvorbis/libmp3lame 天生不支持多线程，提速靠多 worker）
- 显卡加速不做（用户知悉）：视频是默认关的实验功能，且 GPU 编码
  同画质体积更大，与瘦身目标冲突
- Cadaver 实测：514MB 成品全流程（解压+扫描+优化+回包）48 秒，
  结果与旧并行度完全一致（省 303.8MB）；回归测试 101 项全绿

## 2026-08-17 默认档画质优先 + 小文件也榨（用户拍板）

- 默认档位 balanced → conservative（config/cli/web/前端四处硬编码
  同步改为引用 DEFAULT_PRESET，前端两个下拉框 selected 同步迁移，
  四语文案更新：推荐标识移到保守档）
- 保守档强化：q95 近视觉无损 + 开启 WebP 转换（q95 WebP 同样近无损
  但显著更小）；三档体积门槛统一降到 1KB——几十 KB 小图转 WebP
  后只剩几 KB 且数量多，不再跳过；F8 APK 自动转发档改回 conservative
  与原注释名实相符
- Cadaver 实测对比（514MB 成品，同一入口）：画质优先档省 257.2MB/处理
  图片 450 项；均衡档省 304.0MB/429 项（旧默认 303.8MB/368 项）——
  小文件覆盖显著增加，画质优先的体积代价约 46MB（q95 vs q85 的必然结果）
- 回归测试 103 项全绿（新增默认档画质优先 + 小文件门槛 2 条）

## 2026-08-17 继续压榨批次 + CSE 视频样本实测（用户拍板）

- 补漏：APK 侧 MP3 降码率重编码（成品侧早已支持，APK 侧漏了）；
  交付 zip 压缩等级 6→9（包内大头已是压缩格式，收益微小但零风险）
- CSE-2.1.0-pc 实测（981MB 资源，视频封 movies.rpa + unpacked 双存在）：
  开 --videos 后 **省 634.8MB**；ED.webm 226.5MB→79.9MB（-65%）；
  封包内与散文件两处同名视频都被正确压缩，3 个 rpa 重建成功；
  视频功能默认关（实验性）维持不变，遇带视频的游戏手动勾选
- 观察：LXGWWenKai-Bold.ttf 字体瘦身 fontTools 报 array index out of
  range，兜底逻辑正确（保留原文件+警告），属待确认类小问题暂不动

## 2026-08-17 视频编码优化与格式决策（用户关切"有没有更省的格式"）

- **AV1 不采用（决策记录）**：AV1 同画质比 VP9 再省 30%+，但 Ren'Py
  引擎自带 ffmpeg 不保证能放 AV1，开场动画放不出是灾难——坚持
  "同名同格式、只换编码参数"，x264/VP9/theora 是引擎确定能放的上限；
  体积/画质的选择交给全局档位（CRF 三档已覆盖），不另加视频档位
- VP9 编码加 `-row-mt 1`（行级多线程）：编码快数倍，画质/体积不变，
  零风险；CSE 小视频实测验证参数有效
- **更正（同日官方文档研究后推翻上条）**：见下节
- **GitHub 根目录文件整理结论**：根目录的 LICENSE/README×4/CONTRIBUTING/
  CODE_OF_CONDUCT/SECURITY 是 GitHub 社区规范文件，靠根目录位置被
  自动识别（About 栏/Community profile），**移进文件夹反而失效**；
  项目文档早已在 docs/；待办：AGENTS.md 与 docs/AUDIT-2026-08-17.md
  尚未纳入 git，随下次提交一起入库

## 2026-08-17 Ren'Py 视频编码官方研究（推翻上节 AV1 结论）

- 研究依据：官方文档 renpy.org/doc/html/movie.html + 本地引擎二进制
  字符串扫描（游戏/SDK 的 librenpython.dll 均含 AV1/HEVC 解码器串）
- **官方支持清单**：视频 AV1 / VP9 / VP8 / Theora / MPEG-4 part 2
  (Xvid/DivX) / MPEG-2 / MPEG-1；容器 WebM / Matroska / Ogg / AVI /
  MPEG 流；音频 Opus / Vorbis / MP3 / MP2 / FLAC / PCM
- **官方明确不支持 H.264 解码（和 AAC）**：H.264+MP4 组合仅 Web
  平台靠浏览器解码幸存——之前"AV1 不保证能放"的推断错误，真正
  的风险反在 H.264 上
- 落地改造（video_optimizer 重写）：
  ① 新增 probe_video_codec（ffprobe 探原编码），"同编码重编"原则：
     mp4 仅当原本就是 H.264 才按 H.264 重编（不新增风险），HEVC/
     Xvid 等 mp4 与清单外 webm/ogv 一律不动并告警；
  ② 新增实验选项 experimental_av1（CLI --av1 / 界面高级选项，四语
     文案）：把非 AV1 的 webm 转成 AV1（SVT-AV1 编码），带"仅
     Ren'Py 8.0+ 能放"警告；默认关；
  ③ 原编码本就是 AV1 的视频自动维持 AV1 重编（零兼容风险，不需
     用户勾选）——CSE 样本实测发现其视频全是 AV1，旧逻辑把它们
     转成 VP9 属于白费劲
- CSE 实测对比（hrt_1.webm 3.6MB，保守档）：AV1 同编码重编
  -9%（3692→3361KB）耗时 2.3s；VP9 转码仅 -2% 耗时 6.5s
- 回归测试 107 项全绿（新增同编码拦截/放行决策 4 条）；ruff 通过

## 2026-08-17 反编译解锁 + 包回 rpa（用户拍板"精益求精"）

- **vendor 引入 unrpyc v2.x**（MIT）到 rtools/vendor/unrpyc/，
  THIRD_PARTY_NOTICES 新增内嵌源码节署名；CI ruff 排除 vendor
  （上游代码保持原样）；新增 rtools/decompile.py 封装：跳过已有
  源码、单文件失败容错（对应资源自动退回同名保守策略）
- rpa.rebuild_archive 支持改名替换：替换值可为 (新名, 本地路径)
  元组——旧条目剔除、新名入包（"按原样包回 rpa"，用户拍板默认行为）
- run_dist 新增实验开关 experimental_decompile（CLI --decompile /
  界面高级选项，四语文案，默认关）：反编译散落与封包内全部脚本，
  封包内脚本产物拷回 game/ 对应位置（引擎加载优先于封包内同名
  rpyc，自动重编译）；IMAGE/AUDIO 转换分支解锁 in_rpa，转换产物
  改名入包 + 引用同步改写；重建失败时散落副本保留作兜底
- CSE 实测（--decompile --videos，479 秒）：**省 635.7MB**；
  images.rpa 替换 134 个文件（含 29 张包回 webp）、audio.rpa 158 个、
  movies.rpa 14 个；反编译 59 个 rpy；引用改写验证通过
  （如 liluo_common/common/fastwork_01.webp）
- 回归测试 109 项全绿（新增 rebuild 改名替换 + 反编译往返 2 条）
- 待办提醒：exe 打包需把 rtools/vendor/ 纳入 PyInstaller 数据
  （RenPySlim.spec / build_exe.bat，下次发版前处理）

## 2026-08-17 多语言四语上线（用户四步计划）

- README 全量重排版（中文默认），新增 README.en/ru/es 三份翻译，
  各 README 顶部语言行互链
- 界面四语：语言选择器改 select + LANGS 注册表（选项由注册表生成），
  浏览器语言自动识别（localStorage 手动选择 > navigator.language > 默认 zh）；
  ru/es 字典完整手写，LOG_PATTERNS 日志模板同步四语化
- 前端 TS 咨询拍板：**不引 TS**（维护单文件零依赖架构），替代方案为
  tests/test_i18n.py 键完整性守卫（字典键集一致 + data-i18n 键全覆盖）；
  抓出过 en 缺 nav_apk/title_apk、ru/es 缺 pwd_ph/browse_font 的真漏键
- 贡献翻译指南进 CONTRIBUTING.md（新增语言四步：字典/注册/日志模板/文档）
- 坑：解析 JS 字典顶层键必须字符串感知（值里 {n} 占位符会干扰花括号配对）；
  一行多键写法不能靠行首正则提键
- **v0.11.0 已发版**（tag 触发自动流水线，exe 已挂 Release）；首次触发挂在
  sanity 测试缺 pytest/httpx，修复后重打标签成功。发版命令不变：
  git tag v0.x.0 && git push origin v0.x.0
- 会话环境坑：上下文压缩后沙箱可能把高风险标记的 git 推送路由进断网沙箱
  （remote-https Permission denied）；重发标签时用户手动推了一条
  （git push --force origin v0.11.0）。新会话接手时沙箱应已恢复

## 2026-08-16 v0.11.0（用户拍板“把值得做的搞定”）

- APK 瘦身进图形界面：新增“APK 瘦身”导航页，小白三步流（选 APK →
  选方案：档位/最大瘦身开关/签名三选一默认自动造钥匙 → 开始）；
  后端 /api/slimapk 与 CLI 同引擎；选 .apk 文件时全部入口自动路由到该页
- F8 完成：压缩包里装的是 APK 时，成品瘦身流程自动转入 APK 安全档
  （同名压缩、不换格式、不签名，并提醒想用全力请用 APK 专页）
- 实测：/api/slimapk 真跑 Cadaver 554MB APK，省 160.4MB/323 处/自动签名成功；
  产物 Cadaver\无密码的单独apk-瘦身.Apk 即真机验收用包，钥匙在
  Cadaver\renpyslim.keystore（密码在旁边备忘文件）
- 待办：用户真机验收（装包听背景乐/音效，验证音频重映射——建议用
  --remap 版本另打一包再验，当前交付包是安全档）；v0.11.0 发 Release（待用户点头）

## GitHub 自动化配置（2026-08-17 就位，用户拍板全套）

- **CI**（.github/workflows/ci.yml）：每次提交/PR 自动跑 ruff 真错误检查
  （--select F,E9，风格类不拦）+ 全量 pytest；windows-latest + Python 3.13。
  已踩坑记录：①CI 机无 FFmpeg/SDK，依赖它们的测试必须带 skip 保护；
  ②本地 ruff --fix 的改动要确认全部加进提交（曾漏两个测试文件导致 CI 红）
- **后端本地防护**（用户拍板加）：web/app.py 中间件 guard_local_only
  核对 Host/Origin，非本机来源一律 403（防 DNS 重绑定/恶意网页
  指挥本地服务）；测试用 fastapi TestClient + base_url 指定本机
  Host（默认 testserver 会被自己的防护拦下），需 httpx 依赖
- **Release**（release.yml）：打 v* 标签自动构建 exe + 发 Release
  （发版命令：git tag v0.x.0 && git push origin v0.x.0，全自动）
- **CodeQL**：用用户在网页开的仓库级默认扫描（default setup）；自定义
  workflow 与其互斥已删除，别再建 codeql.yml
- **Dependabot**：pip 只收安全更新（纯版本 bump 已配 ignore，因
  requirements.txt 用宽容下限是刻意设计）；github-actions 正常收升级 PR
- 用户曾在网页模板市场误开 Fortify/Conda 模板，已删除；再遇类似“其他
  CI workflows”模板先核实再留
- CodeQL 首批 16 警报处理完毕（开放清零）：1 个真问题（ci.yml 缺最小权限
  声明，已修）；11 个 py/path-injection 为本地工具典型误报（API 只听
  127.0.0.1，路径来自本机用户就是工具本分），带理由关闭；1 个密码明文
  备忘为拍板过的产品设计，带理由关闭。再遇 path-injection 警报先按此
  模型核实；dismiss API 参数是 dismissed_reason/dismissed_comment，
  且 comment 限 280 字符

## Cadaver 样本实测战绩（用户提供的真实游戏，全部实测通过）

### 2026-08-16 v0.10.0 复测（修复后重跑，产物在 _cad_v10\）

| 样本 | v0.10 结果 | 对照 v0.9 |
|---|---|---|
| 无密码 APK 同名瘦身 | 省 160.4MB，323 处改动，字符集提取 2536 字（修复实锤，旧版链路断裂） | 新增验证 |
| 无密码 APK --remap | 554.1→151.8MB，381 资源换格式 | 旧版 148.7MB；差 3.1MB = 旧版把字体剃成保底集的“假收益”，新版字体完整 |
| 电脑成品.zip 直进直出 | 省 318.6MB，charset 2517，自动回包 | 旧版 303.9MB，多省来自字体修复 |
| 工程全流程（优化+PC打包） | 省 306.8MB，打包成功 297.7MB，真实 lint 通过 | 旧版 lint 实为空转假象 |
| 【安卓】密码 zip（2510） | 解压链路正常，内装 APK 同名瘦身省 160.4MB | 同口径 |

复测新发现（已修）：lint_project 拿相对路径在 SDK 目录下找不到工程，
空转还报“通过”假象 → 入口转绝对路径，新增回归测试，共 69 项全绿。
新 backlog：压缩包内直接装的是 APK 时 optimize dist 应自动转入 APK 流程
（当前报“找不到成品目录”，需先手动解包再 slimapk）。

### 2026-08-15 v0.9.0 首测（修复前基线）

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
