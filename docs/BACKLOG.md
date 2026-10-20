# RenPySlim 需求存档（BACKLOG）

> 本文件是项目唯一的需求/待办事实源。任何新功能想法先记录到这里，
> 注明来源、价值、风险、状态，再决定是否实施。
> 状态：✅ 已完成 ｜ 📅 已排期 ｜ 💡 待评估 ｜ ❌ 明确不做

## 一、已完成的核心能力（基线）

| 能力 | 说明 |
|---|---|
| 资源分析报告 | 体积/问题/建议/优先级，工程与成品两种模式 |
| 图片优化 | PNG 无损优化、JPG/WebP 重编码、PNG→WebP 转换（引用门控） |
| 音频优化 | WAV/MP3→OGG（工程模式）、OGG/MP3 降码率（成品模式） |
| 字体瘦身 | fontTools subset，保底字符集四层防线，空壳防御；多语言感知（按翻译语言分桶 + 字体三档定向瘦身，v0.16.0） |
| 引用改写 | 长路径优先、左守卫防误伤、编码无损往返 |
| RPA 封包 | 纯 Python 拆/建，RPA-2.0/3.0 新旧写法自适应，pickle 白名单 |
| 官方打包 | 委托 SDK：distribute（PC/Mac）、android_build（APK）、封 RPA 配置注入 |
| 成品瘦身 | 同名压缩 + 封包重建 + 垃圾清理 + 无引用检测；带 .rpy 源码自动解锁格式转换 |
| 压缩包直进 | zip/7z 原生，RAR 依赖 7-Zip 并给指引；瘦身后自动回包 |
| 独立字体瘦身 | 选字体+文本来源，TTC/OTC 拆分输出，字符清单导出 |
| 轻松操作 | 拖拽到图标、浏览选择框、最近路径、托盘常驻、退出按钮、扫描进度 |

## 二、借鉴清单（来自五个参考项目的侦察结论）

### 第一批（低风险高收益，已完成）

| # | 需求 | 来源 | 结果记录 | 状态 |
|---|---|---|---|---|
| B1 | RPA 头部多密钥兼容 | renpak | rpa.py 头部密钥字段全部异或，新增双密钥构造测试 | ✅ |
| B2 | 重建封包沿用原密钥 | renpak | rebuild_archive 透传源包 key，新增测试 | ✅ |
| B3 | 报告口径诚实化 | Rentool | 失败/压不动文件计数（failed 字段）+ 警告文案，不计入节省 | ✅ |
| B4 | 音频/图片并行处理 | Alenia-Porter | 工程线与成品线均已并行（ThreadPool，并行度 = 核心数-2，上限 16；视频每任务分半核，音频每任务 1 线程靠多路并发）；28 核机实测 514MB 成品全流程 48 秒。工程线字体瘦身也于 2026-08-25 并行化（复用同款任务框架，计划预先定好任务只执行），并行残余债清零 | ✅ |
| B5 | PNG 深度压缩档 | ultra-tinypng 思路 | quantize_png（本地量化，同名安全），界面/CLI 实验开关，新增测试 | ✅ |

### 第二批（中等工作量，已完成）

| # | 需求 | 来源 | 结果记录 | 状态 |
|---|---|---|---|---|
| B6 | 增量缓存 | Alenia-Porter | cache 模块（内容哈希+动作键），图片/音频命中跳过，界面可禁用 | ✅ |
| B7 | 视频压缩 | Alenia-Porter / Rentool | video_optimizer（mp4/webm 同名重编码分档 CRF），默认关闭实验项，新增测试 | ✅ |

### 待实战触发 / 实验性

| # | 需求 | 来源 | 说明 | 状态 |
|---|---|---|---|---|
| B8 | 安卓打包补丁 | renpybox | 实战已通过：用测试工程 JIGSAW_PUZZLES 成功打出 release 签名 APK（271MB）。此前 the_question 失败的根因是该项目没配密钥；四个补丁均未用上，暂不引入 | ✅ |
| B9 | 动态拼名运行时兜底 | renpak | 实验项已真实验收：无源码成品 Jigsaw_Puzzles-1.5.5 开启后 16 张图透明转 WebP，共省 160MB，游戏启动正常；删脚本即还原 | ✅（已验收） |

### 远期想法（部分已实现）

| # | 想法 | 来源 | 状态 |
|---|---|---|---|
| F1 | APK 本体瘦身（拆包→同名压缩/图转WebP音转OGG重映射→重打包保字节→三种姿势重签名） | Rentool | ✅ 已实现（cli slimapk）；--remap：图转 WebP、音转 OGG + SDK 编译重映射脚本注入（B9 的 APK 版）；签名三姿势含 --gen-key；签名路径已防乱码坑 | 
| F2 | 自更新检查（GitHub Releases 对比 + 界面提示） | Alenia-Porter | ✅ 已实现 |
| F3 | 崩溃转储（~/.renpyslim/crashes/，保留 20 份） | Alenia-Porter | ✅ 已实现 |
| F4 | 任务取消按钮（扫描/优化均可停，已完成成果保留） | Alenia-Porter | ✅ 已实现 |
| F5 | lint 输出驱动的自动修复闭环 | renpybox | 💡 无排期 |
| F6 | 进度字节级（处理中 x/y · 累计已省 X MB） | Rentool | ✅ 已实现 |
| F7 | Ren'Py TextMate 语法资产（若未来加脚本查看器） | Rentool | 💡 无排期 |
| F8 | 压缩包内直接装 APK 时自动转入 APK 瘦身流程 | Cadaver 复测发现 | ✅ v0.11 已实现（安全档自动转，全力档引导用 APK 专页） |
| F9 | 多语言感知字体瘦身（语言识别 + 按语言分桶 + 字体处数统计 + 行内标签字体精确瘦身 + 语言定向瘦身） | 2026-08-25 用户拍板：属字体瘦身第一天起的设计缺口；同日二次拍板：删除单语言发行过滤（用本工具的就是发多语言大包），只保留多语言口径下的定向优化 | ✅ v0.16.0 已实现：按语言分桶扫描（无开关，自动生效）；字体三档瘦身（行内标签精确 / 语言定向 / 全量兜底）；缺字提醒按字体实际生效字符集对账；报告含 languages / font_usage（含语言归属）/ font_slim_modes；拿不准一律回退全量字符集；已移除早期的 --language 选项与界面语言下拉框 |

## 三、明确不做（红线与取舍记录）

| 项 | 原因 |
|---|---|
| rpyc 反编译重建成品 | 用户拍板不做：还原失真风险不可控，违背"不动成品脚本"安全底线；将来改主意可重启评估 |
| AVIF 图片路线 | 引擎不原生支持，需注入解码器，风险大于收益；WebP 路线已足够 |
| 在线压缩服务 | 与"全本地处理"定位冲突；绕限额手段违反服务条款 |
| 翻译/本地化功能 | 偏离瘦身打包使命（renpybox 的主业不是我们的方向） |
| 照抄参考项目代码 | 多处代码质量差（如文件名清洗会毁掉中日文文件名），只借鉴思路 |

## 四、维护约定

- 新需求先落到本文件，注明状态，再动代码
- 每批完成后把 📅 改为 ✅ 并补一行结果记录
- 红线变更必须由用户拍板，拍板结论记录在"明确不做"或移回待办
- 已知残余债：工程/成品全线并行与字体瘦身并行已清零；v0.17 批次遗留的
  “运行态任务表仍内存”“日志翻译覆盖度”边界见第六节

## 五、维护研判存档（防下次重复研判）

### 依赖与供应链（2026-10-13 v0.16.2）

| # | 事项 | 研判结论 | 状态 |
|---|---|---|---|
| D1 | 内嵌 unrpyc 从 v2.0.3 同步到上游 v2.0.4 | 上游 2.0.4 只做了两件事：把 corrupy 从内嵌子模块改成外部 PyPI 依赖 + 修 `un.rpyc` 构建路径的回归。本工具只程序化调用反编译内核、不用 un.rpyc 那条路径；同步的代价是新增运行时依赖 corrupy 并扩大 exe 体积。已查清后判为“价值低、代价真实”，不同步 | 💡 待评估（下次上游有反编译内核修正时一并抬） |
| D2 | starlette TestClient 提示“用 httpx 已弃用，请装 httpx2” | 先实测再采纳：本机**卸掉 httpx、只留 httpx2** 后全量测试依旧绿，且仓里没有任何代码直接 `import httpx`（只有 starlette 的 TestClient 在用）——说明 httpx 已可直接汰。换后 StarletteDeprecationWarning 消失，测试仅余一条由用例故意造的 zipfile 重名告警 | ✅ v0.16.2 后续补债已修：dev 清单 httpx → httpx2（实测无 httpx 环境跑绿后才换） |
| D3 | 要不要给 CI 加“按下限装最旧依赖”的第二路矩阵 | 能暴露“下限写了但从没测过”的问题，但本仓已把下限抬到实测通过版、又有 lock 冻结与 test_deps_lock 对账，收益下降；个人项目一台 windows runner 的开销不便宜 | 💡 待评估（出现“用户装不到可用依赖”的真实反馈再开） |

## 六、2026-10-21 v0.17.0 全量功能批次（已完成）

用户拍板“值得加入的全部加入”：项目要同时服务**开发者**（发布前优化 + 打包）与
**普通用户**（拖入成品/APK 即可瘦身），CLI 与 GUI 两条能力面都要做完整；新增能力
两头都要有入口、口径一致。本批次按“底层先合、前端后叠、最后统一验收”三波施工，
每个工包独立 worktree、分支与文件所有权不重叠。

| # | 能力 | CLI 口径 | GUI 口径 | 结果记录 | 状态 |
|---|---|---|---|---|---|
| G1 | 工程“只优化不打包” | `optimize` 工程模式即此用途（不需要 SDK）；`full` / `package` 空平台与未知平台在查 SDK、建目录、跑优化之前报错 | 项目页 `opt_only` 走 `/api/optimize`；平台全空前端拦一次、后端 `/api/package`、`/api/full` 再拦一次（400） | `packager.validate_platforms` 为 CLI/Web 唯一口径；`cli._validated_platforms` 前置调用；回归 test_cli_v017 / test_packager_platforms / test_web_v017 | ✅ |
| G2 | 结果与报告动作 | 现有 JSON / 清单文件格式不变 | 结果区与报告区可打开输出目录、资源管理器定位文件、复制路径；分析报告导出 JSON / CSV（全量，不受表格 300 行截断影响）；超 300 行显示截断提示；APK 预分析同样可导出 | `/api/reveal` + `revealPath` / `copyPath` / `exportReport` / `reportToCSV`；导出遍历全量 `entries`，表格只渲染前 300 行；test_frontend_v017 锁住 | ✅ |
| G3 | APK 只读预分析 | `analyze <apk>` 自动识别（或 `--mode apk`）；`report.mode=apk`，顶层 `apk` 含 apk_size / total_entries / optimizable / size / script_count / signed / est_saving / est_saving_remap / remap_gain / has_renpy_layout；非 APK 传 `--mode apk` 直接失败 | APK 页新增“扫描分析”卡，先看可优化资源与两套预计节省再开跑 | `apk.analyze_apk` 只读 ZIP 条目表，不解包、不建工作目录、不改原包；Web `/api/analyze` 按后缀路由；test_apk_analysis / test_cli_v017 / test_web_v017。数字是估算，实际节省以瘦身结果为准 | ✅ |
| G4 | 危险操作与磁盘安全 | 复制工作副本、in_place 备份、APK 解包/回写前统一空间预检，不足抛人话错误 | `in_place` 执行前二次确认弹窗（默认焦点在取消） | `backup.ensure_free_space`（另加 10% 余量，读不到磁盘信息只告警）；`apk.slim_apk` 解包/写回前调用；`confirmDanger` 模态；test_backup_space / test_frontend_v017 | ✅ |
| G5 | 任务历史持久化 | 新增 `history [--limit 1..50] [--clear]`；analyze / optimize / full / package / slimapk / slimfont 六类任务命令也写入同一份历史，`env` / `history` 不记录 | 设置页任务历史卡（默认 20 条、刷新、清空、定位产物）；重启后可查已完成/中断任务 | `rtools/jobstore.py` 落盘 `~/.renpyslim/jobs.json`（`RENPYSLIM_HOME` 可改目录），最多 50 条；心跳 >180 秒判陈旧 + 进程判活改判 interrupted；CLI 摘要只抽 saved_bytes/产物路径/APK stats 等小字段；Web `/api/history` GET/DELETE；test_jobstore / test_cli_history / test_cli_job_history / test_web_v017 | ✅ |
| G6 | 结构化日志翻译 | 保持人话中文日志；`Progress.emit` 宽签名回调兼容，旧两参回调行为不变 | Web 日志携带 `code` + `params`，前端按结构化 code 做八语翻译；未覆盖项回退原文/旧句式 | pipeline / apk / font_tool 核心 code 清单由 test_log_codes 锁定（snake_case、不跨模块/跨 stage 重复）；test_progress_events、test_web_v017 覆盖；**边界：警告与未被翻译层覆盖的日志仍可能显示中文原文** | ✅ |
| G7 | 可访问性 | — | 导航改原生按钮 + `aria-current`、焦点可见、读屏标签与 `aria-live` 状态播报、尊重 reduced-motion、亮色主题对比度修正 | index.html / styles.css / app.js 落地；test_frontend_v017 校验关键 aria 钩子；styles.css 提供 `:focus-visible`、`prefers-reduced-motion` 与亮色对比度令牌 | ✅ |
| G8 | 前端完成度 | — | 三件套拆分、首屏三步引导条、主区宽度放宽（max-width 1120px）、窄窗口侧边栏图标化、版本/i18n 文案细节统一 | index.html 只留结构与 `data-i18n`；styles.css 负责响应式与引导条；test_frontend_syntax 校验拆分引用与加载顺序 | ✅ |
| G9 | 代码优雅度 | — | 前端拆成零构建 styles.css / i18n.js / app.js，不引入打包器 | 三件套拆分完成；`node --check` 通过（无 Node 时 test_frontend_syntax 自动跳过）；拆分后缺键/语法漂移有测试兜底 | ✅ |
| G10 | 验收与文档 | 新命令进 AGENTS / README（中英同步） | 八语界面键齐全 | AGENTS 中英双节同步 `history` / APK 预分析 / 只优化 / 平台校验 / 磁盘预检 / 历史隐私 / 结构化日志；README、ARCHITECTURE、STATUS、CONTRIBUTING 收口；版本号 0.17.0；issue 模板版本示例同步。**边界：七份翻译 README 本轮不动**（中文事实源先行，翻译批次后续跟进） | ✅ |

批次验收线：`python -m pytest tests -q` 全绿（数量以实际收集为准）；ruff F/E9 全绿；
`python -m pip check` 无冲突；前端三件套 `node --check` 通过（无 Node 环境自动跳过）；
文档中的命令、路径与参数逐条对照实现。
