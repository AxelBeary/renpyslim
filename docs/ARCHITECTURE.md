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
  app.py           FastAPI：API 层（薄），任务管理（JOBS 内存表 + 轮询）
  static/index.html 单页界面（原生 JS，无构建步骤——刻意为之）
rtools/            核心引擎（不依赖 Web/CLI，可独立调用与测试）
  models.py        数据结构：AssetInfo/Issue/AnalysisReport/ChangeRecord/Progress
  config.py        档位（conservative/balanced/aggressive）、保底字符集、选项
  scanner.py       资源扫描（两步：先数总数再逐个报进度），ffprobe 元数据
  analyzer.py      报告生成：问题/建议/优先级/预计节省
  charset.py       字符集提取（工程/成品/独立三种来源）、缺字对账、健壮编码
  font_optimizer.py 字体瘦身（临时文件策略 + 空壳防御 _sanity_check）
  font_tool.py     独立字体瘦身流程（TTC/OTC 拆分、字符清单导出）
  image_optimizer.py 图片优化（临时文件策略，没变小不替换）
  audio_optimizer.py FFmpeg 转码（run_quiet 无窗口）
  refs.py          RefIndex：引用查找与改写（左守卫、长路径优先、编码往返）
  cleanup.py       垃圾清理、重复检测、无引用检测、隔离区
  verifier.py      官方 lint 验证
  rpa.py           RPA 封包读写（白名单反序列化，新旧格式自适应）
  archives.py      zip/7z/RAR 解压、成品目录定位、回包
  packager.py      SDK 发现、官方打包调度、RPA 归档配置注入、用户配置
  pipeline.py      编排层：run_project（A线）/ run_dist（B线）/ run_dist_smart
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

## 4. 安全红线（修改任何代码前先读这里）

1. **原件不动**：一切改动先落在工作副本；in_place 必须先强制备份 zip
2. **没变小不替换**：所有优化器先写临时文件，比较后才 replace
3. **引用门控**：查不到字面引用的资源绝不改名（图片因引擎自动加载机制
   永远不参与废资源判定与改名）
4. **引擎目录保护**：成品模式 `renpy/`、`lib/` 一律不碰
5. **隔离不删除**：无引用文件只移入 `_rtools_quarantine`，永不直接删
6. **空壳防御**：字体瘦身结果与预期字形数偏差过大即拒绝，原字体保留
7. **编码无损往返**：脚本读写用 utf-8 → gb18030 降级链，禁止 errors="replace" 后回写
8. **pickle 白名单**：RPA 索引反序列化只放行基础类型 + _codecs.encode
9. **外部调用无窗口**：所有 subprocess 走 procutil.run_quiet

## 5. 扩展指南

### 新增一种优化器（如视频压缩，见 BACKLOG B7）
1. 建 `rtools/video_optimizer.py`，遵循"临时文件+没变小不替换"范式
2. `config.py` 加选项字段（默认关 = 安全）
3. `pipeline.py` 两条线的优化循环各接一个分支
4. CLI/Web 加对应开关，tests/ 加回归测试
5. 更新 BACKLOG 状态

### 新增一个界面区块
index.html 是单文件原生 JS：卡片（card）+ switchMode 控制显隐，
任务类操作统一走"POST 提交 → /api/job/{id} 轮询日志"模式，勿改回同步阻塞。

### 修改 RPA/封包格式相关代码前
必须读 rtools/rpa.py 头部注释（官方格式对照），改后必须补
"非空前缀条目 + 多段条目"两类用例的测试。

## 6. 测试与构建

```
.venv\Scripts\python -m pytest tests -q      # 全量回归（提交前必跑）
.venv\Scripts\python -m compileall -q rtools cli.py web/app.py   # 快速语法检查
build_exe.bat                                # 产出 dist\RenPySlim.exe
```

- 测试文件按功能命名：test_core（RPA/引用/字体/图片）、test_features（第二批功能）、
  test_archives、test_font_tool、test_progress、test_empty_shell、test_rpy_dist
- 真实样本 E2E 惯例：优化 → lint/启动验证 → 对比体积，产物路径在 _sample/
- exe 首次启动慢属杀软扫描正常现象，勿误判为卡死

## 7. 已知技术债与重构触发器

| 债务 | 触发重构的条件 |
|---|---|
| pipeline.py 中工程/成品两套优化循环存在重复 | 新增第三类优化器或第三种模式时，抽取统一 optimize_asset 层 |
| 任务状态存内存（JOBS），重启即失 | 若要做任务历史/断点续跑，落盘为 SQLite/JSON |
| 单文件 index.html 体积渐大 | 超过 ~1500 行时按卡片拆分 JS 片段（仍不引入构建工具） |
| 进度仅文件计数 | 实施 BACKLOG F6 时一并升级为字节级 |

## 8. 约定

- 用户面向文案：人话，错误必须带"下一步怎么办"
- 新文件必须有模块 docstring；公开函数写明安全语义
- 版本号在 `rtools/__init__.py`（__version__），API /api/env 与 CLI --version 暴露
- 任何拍板结论（做/不做）当天回写 BACKLOG.md
