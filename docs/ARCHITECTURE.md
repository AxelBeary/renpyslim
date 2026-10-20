# RenPySlim 架构说明书（面向维护者与 AI agent）

> 读完本文件应能回答：每个模块干什么、数据怎么流、哪些红线不能碰、
> 加新功能从哪下手、怎么测试和打包。需求与待办见 [BACKLOG.md](BACKLOG.md)。

## 1. 项目定位与形态

Ren'Py 游戏资源瘦身与打包工具。**一台引擎、两个入口**：

- 图形界面：`main.py` 启动本地服务（uvicorn + FastAPI）→ 浏览器打开 `web/static/index.html`
- 无头模式：`cli.py`，全程 JSON 输出（stdout），日志走 stderr

设计公理（一切决策的根）：**默认安全、可反悔、没变小不动手、原件优先**。

## 2. 目录与模块职责

```
main.py            GUI 入口：端口选择、单实例转发、托盘、浏览器拉起
cli.py             无头入口：argparse 子命令 → 同一套引擎
web/
  app.py           FastAPI：API 层（薄），任务队列（JOBS 内存表 + jobstore 历史）
  static/index.html 三件套宿主页：HTML 结构 + 首屏防闪内联脚本
  static/styles.css 样式：设计令牌、亮暗主题、可访问性（焦点/减动效/对比度）
  static/i18n.js   八语字典、日志句式翻译、语言/主题注册表
  static/app.js    业务逻辑：导航、分析/优化/APK/字体流程、结果渲染
rtools/            核心引擎（不依赖 Web/CLI，可独立调用与测试）
  models.py        数据结构：AssetInfo/Issue/AnalysisReport/ChangeRecord/Progress
  config.py        档位（conservative/balanced/aggressive）、保底字符集、选项
  scanner.py       资源扫描（两步：先数总数再逐个报进度），ffprobe 元数据
  analyzer.py      报告生成：问题/建议/优先级/预计节省
  charset.py       字符集提取（工程/成品/独立三种来源）、缺字对账、健壮编码；
                   按翻译语言分桶与引擎内置界面安全字（多语言感知，v0.16.0）
  font_optimizer.py 字体瘦身（临时文件策略 + 空壳防御 _sanity_check）
  font_tool.py     独立字体瘦身流程（TTC/OTC 拆分、字符清单导出）
  image_optimizer.py 图片优化（临时文件策略，没变小不替换）
  audio_optimizer.py FFmpeg 转码（run_quiet 无窗口）
  video_optimizer.py 视频重编码（实验性，默认关；同编码安全重编，AV1 实验选项）
  refs.py          RefIndex：引用查找与改写（左右守卫、长路径优先、编码往返）
  cleanup.py       垃圾清理（限已知安全位置）、重复检测、无引用检测、隔离区
  verifier.py      官方 lint 验证（全分支 suspects 键、cp936 解码回退）
  rpa.py           RPA 封包读写（白名单反序列化，新旧格式自适应，改名替换重建）
  archives.py      zip/7z/RAR 解压（GBK 中文名还原、密码编码重试）、成品定位、回包
  packager.py      SDK 发现、官方打包调度（平台非空/合法校验）、RPA 归档配置注入、用户配置
  apk.py           APK 瘦身（x- 前缀体系、重打包保字节、重映射注入、三种签名）
                   与只读预分析 analyze_apk（读 ZIP 条目表，不解包不改原包）
  remap.py         运行时重映射脚本生成（B9，py2/3 双兼容）
  decompile.py     脚本反编译封装（unrpyc，解锁无源码成品的格式转换）
  vendor/unrpyc/   内嵌第三方源码：unrpyc v2.x（MIT，署名见 THIRD_PARTY_NOTICES）
  cache.py         增量缓存（SHA-256 + 动作键，原子写入，2GB 淘汰）
  backup.py        工作副本与强制备份 + ensure_free_space 磁盘预检（含 10% 余量）
  jobstore.py      任务历史持久化（~/.renpyslim/jobs.json，心跳 + 进程判活）
  crashdump.py     崩溃转储（~/.renpyslim/crashes/，保留 20 份）
  updater.py       GitHub Releases 自更新检查（失败静默）
  pipeline.py      编排层：run_project（A线）/ run_dist（B线）/ run_dist_smart；
                   并行任务调度（_run_jobs）与取消支持
  utils.py         fmt_size 等公共小工具
  runtime.py       端口登记文件、干净退出
  procutil.py      run_quiet：Windows 下外部调用不弹黑框
tests/             pytest：conftest.py 统一 sys.path；按功能分文件
docs/              BACKLOG.md（需求事实源）、本文件
assets/            图标资源（icon.ico/icon.png，exe 与托盘共用）
```

依赖方向（只许从上层指向下层）：
`main.py / cli.py / web/app.py → rtools.pipeline → 各功能模块 → models/config/procutil`
**rtools 内部禁止反向依赖 pipeline**；新增模块放在功能层。

## 3. 两条主流程

### A 线（工程）run_project
复制工作副本（或 in_place 先强制备份）→ 扫描 → 字符集+缺字对账 →
附加检测（废资源/重复/缺字，只报告）→ 图片/音频/字体优化 →
引用改写（仅转换成功的进 rename_map）→ 垃圾清理 → 可选隔离 →
官方 lint → 输出 analysis.json / changelog.json / charlist.txt / validation.txt
→（full 模式续接 packager 打包，可注入 RPA 归档配置）

### B 线（成品）run_dist / run_dist_smart
压缩包输入先解压定位 → 复制工作副本 → 扫描（散落 + RPA 解包）→
rpy 检测（有源码解锁格式转换）→ 优化（默认同名；RPA 内资源优化后重建封包）→
引用改写（仅带源码时）→ 垃圾清理 → 无引用检测（默认只标记）→
报告 →（smart 模式回包成 zip）

### C 线（APK）apk.slim_apk（实验性）
解开 APK zip 清单 → 只提取 assets/x-game/ 下的图/音/字体（x-renpy 引擎不碰）→
从包内 rpyc 提字符集 → 逐个优化；开了 --remap 则图转 WebP/音转 OGG，
用 SDK 编译重映射脚本（rpyc）注入 x-scripts → 重打包（未动条目逐字节保留，
旧签名移除）→ zipalign 对齐 → 三种姿势签名（原钥匙/自有钥匙/--gen-key 现场造）

### C 线前置：APK 只读预分析（apk.analyze_apk）
`analyze` 识别 `.apk`（CLI 自动识别或 `--mode apk`；Web 的 `/api/analyze`
按后缀路由）→ 只读 ZIP 条目表：统计条目数、可优化资源、脚本数、签名与
Ren'Py 结构 → 复用 analyzer 分别按“默认档”和“开启重映射”算两套预估 →
`report.mode = "apk"`，顶层 `apk` 汇总（apk_size/total_entries/optimizable/
size/script_count/signed/est_saving/est_saving_remap/remap_gain/
has_renpy_layout）。全程不解包、不建工作目录、不改原包；CLI 对非 APK 路径传
`--mode apk` 直接失败。

### 接口层与横切（web/app.py）
- 任务模型：重任务同一时刻只跑一个（JOBS 内存表 + 排队），只读分析不排队；
  `/api/job/{id}` 轮询日志，日志条目含 `stage` / `message` 与可选的
  `code` / `params`（稳定 snake_case 键 + 数值/名称上下文，前端按 code 做
  八语翻译，未覆盖项回退原文/旧句式）。`Progress.emit` 对旧两参回调保持兼容。
- 任务历史：Web 与 CLI 任务命令由 `jobstore` 落盘 `~/.renpyslim/jobs.json`
  （`RENPYSLIM_HOME` 可改目录），最多 50 条；`GET/DELETE /api/history` 与
  CLI `history` 读写同一份记录；`env` / `history` 等查询命令不记录；运行态靠心跳
  （>180 秒判陈旧）+ 进程判活改判 interrupted；只存摘要字段。
- 安全动作：`POST /api/reveal` 只接受“绝对 + 存在”的本机路径，拒绝 UNC/
  网络共享；目录用 explorer 打开，文件用 `/select,` 定位，绝不执行文件。
- 平台校验：`packager.validate_platforms` 是 CLI 与 Web 的唯一口径；空列表
  或未知平台在查 SDK、建工作目录、跑优化之前报错（CLI JSON 失败，Web 400）。
- 磁盘预检：`backup.ensure_free_space` 在复制工作副本、写 in_place 备份、
  APK 解包/回写前检查目标盘空间（另加 10% 余量），不足时抛人话错误。

## 4. 安全红线（修改任何代码前先读这里）

1. **原件不动**：一切改动先落在工作副本；in_place 必须先强制备份 zip
2. **没变小不替换**：所有优化器先写临时文件，比较后才 replace
3. **引用门控**：查不到字面引用的资源绝不改名（图片因引擎自动加载机制
   永远不参与废资源判定与改名）
4. **引擎目录保护**：成品模式 `renpy/`、`lib/` 一律不碰；APK 模式 `assets/x-renpy/` 同样不碰
5. **隔离不删除**：无引用文件只移入 `_rtools_quarantine`，永不直接删
6. **空壳防御**：字体瘦身结果与预期字形数偏差过大即拒绝，原字体保留
7. **编码无损往返**：脚本读写用 utf-8 → gb18030 降级链，禁止 errors="replace" 后回写
8. **pickle 白名单**：RPA 索引反序列化只放行基础类型 + _codecs.encode
9. **外部调用无窗口**：所有 subprocess 走 procutil.run_quiet
10. **空间不足不动手**：工作副本/备份/APK 解包回写前先过 `ensure_free_space`
11. **预分析只读**：APK 预分析只读 ZIP 条目表，不解包、不建工作目录、不改原包
12. **路径动作不执行文件**：`/api/reveal` 只接受绝对存在本机路径（拒绝 UNC/
    网络共享），只用资源管理器打开/定位

## 5. 扩展指南

### 新增一种优化器（如视频压缩，见 BACKLOG B7）
1. 建 `rtools/video_optimizer.py`，遵循"临时文件+没变小不替换"范式
2. `config.py` 加选项字段（默认关 = 安全）
3. `pipeline.py` 两条线的优化循环各接一个分支
4. CLI/Web 加对应开关，tests/ 加回归测试
5. 更新 BACKLOG 状态

### 新增一个界面区块
前端是零构建三件套：`index.html` 只放结构与 `data-i18n` 标记，
`styles.css` 放样式，`i18n.js` 放八语字典与日志句式，`app.js` 放业务逻辑。
卡片（card）+ switchMode 控制显隐；任务类操作统一走“POST 提交 →
`/api/job/{id}` 轮询日志”模式，勿改回同步阻塞。新增文案必须八语齐全
（`tests/test_i18n.py` 与 `tests/test_frontend_v017.py` 会拦缺键）；
新增交互请继续用原生按钮 + aria 属性，并给危险动作套 `confirmDanger`。
装 Node 的环境可跑 `node --check web/static/i18n.js web/static/app.js` 做语法闸
（CI 不强制，测试在无 Node 时自动跳过）。

### 修改 RPA/封包格式相关代码前
必须读 rtools/rpa.py 头部注释（官方格式对照），改后必须补
"非空前缀条目 + 多段条目"两类用例的测试。

## 6. 测试与构建

```
.venv\Scripts\python -m pytest tests -q      # 全量回归（提交前必跑）
.venv\Scripts\python -m compileall -q rtools cli.py web/app.py   # 快速语法检查
node --check web/static/i18n.js && node --check web/static/app.js  # 可选：装 Node 时查前端语法
build_exe.bat                                # 产出 dist\RenPySlim.exe
```

- 前端三件套的语法闸是可选的：`tests/test_frontend_syntax.py` 在 PATH 里
  没有 Node 时自动 skip，有 Node 时必须通过 `node --check`；CI 不额外装 Node。
- 构建配方**只有 `RenPySlim.spec` 一份**（旧名遗留的 RenPyToolbox.spec 已于 2026-10-13
  删除）；build_exe.bat 与 release.yml 都走 `-m PyInstaller --noconfirm RenPySlim.spec`
  （bat 用 `.venv\Scripts\python.exe` 同一条命令，CI 用流水线解释器），
  不得在任何一方内联重复参数（防配方分叉）。
- 依赖双口径：`ci.yml` 按下限装最新（上游破坏性发布的 canary）+ `pip check`；
  `release.yml` 额外以 `-c requirements-lock.txt` 冻结构建。改依赖的四步仪式与
  下限口径见 `docs/CONTRIBUTING.md`「依赖更新」，由 `tests/test_deps_lock.py` 机械对账。
- 测试文件按功能命名：test_core（RPA/引用/字体/图片）、test_features（第二批功能）、
  test_archives、test_font_tool、test_progress、test_empty_shell、test_rpy_dist；
  v0.17 批次另有 test_jobstore、test_cli_history、test_apk_analysis、
  test_backup_space、test_packager_platforms、test_web_v017、test_frontend_*、
  test_log_codes。测试数量以 `pytest tests -q` 实际收集为准，文档不写死。
- 真实样本 E2E 惯例：优化 → lint/启动验证 → 对比体积，产物路径在 _sample/
- exe 首次启动慢属杀软扫描正常现象，勿误判为卡死

## 7. 已知技术债与重构触发器

| 债务 | 触发重构的条件 |
|---|---|
| pipeline.py 中工程/成品两套优化循环存在重复 | 新增第三类优化器或第三种模式时，抽取统一 optimize_asset 层 |
| 运行态任务表仍存内存（JOBS），重启后只剩 jobstore 的历史摘要 | 要做断点续跑/跨重启接管时，把运行态也落盘或引入外部队列 |
| Web 日志翻译依赖 code 覆盖度；未覆盖的 code 与警告回退原文（中文） | 前端翻译层扩键，或后端给新日志补稳定 code 时 |
| app.js 仍偏大（已从 index.html 拆出，零构建不引打包器） | 超过 ~2500 行或出现明显模块边界时，再按视图拆文件 |

## 8. 约定

- 用户面向文案：人话，错误必须带"下一步怎么办"
- 新文件必须有模块 docstring；公开函数写明安全语义
- 版本号在 `rtools/__init__.py`（__version__），API /api/env 与 CLI --version 暴露
- 任何拍板结论（做/不做）当天回写 BACKLOG.md
