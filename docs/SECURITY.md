# 安全政策 / Security Policy

## 支持版本 / Supported Versions

| Version / 版本 | Supported / 支持状态 |
|---|---|
| 0.17.x | ✅ 接收安全报告 receiving security reports |
| 0.10 – 0.16 | ⚠️ 仅按需回移修复，请升级到 0.17.x fixes backported case-by-case; please upgrade |
| < 0.10 | ❌ 请升级 please upgrade |

## 报告漏洞 / Reporting a Vulnerability

**请不要直接开公开 issue。** 请通过以下任一私密渠道报告：

- GitHub 的 **Private vulnerability reporting**（仓库 Security 标签页 →
  "Report a vulnerability"）
- 或在 issue 里只写"存在安全问题"并留联系方式，细节走私渠道

我们承诺：

- **48 小时内**确认收到，**7 天内**给出初步评估
- 确认属实后尽快修复并发版；修复前如需，会发布临时缓解建议
- 报告者可在发布说明中获得致谢（除非要求匿名）

## 安全边界 / Known security boundaries

本项目处理的数据常来自不可信输入（他人打包的成品/APK/压缩包），
已内置的防护包括：

- 服务只监听 127.0.0.1，外网/局域网无法连接；无“开放网络访问”选项，
  不建议修改源码对外暴露（界面无鉴权）
- RPA 索引反序列化白名单（防恶意 pickle 执行代码）
- 压缩包/封包条目路径净化（防 zip-slip 路径穿越）
- Web 接口的路径参数统一归一化后才使用（realpath 折叠 `..`、只收本机绝对路径、
  拒绝 UNC/网络共享与 NUL），"校验的路径"与"使用的路径"始终是同一个（v0.17.1）
- 打开目录/定位文件走系统 Shell API：不拼命令行、不执行文件本身（v0.17.1）
- 引擎目录保护（renpy/、lib/、assets/x-renpy/ 绝不写）
- 隔离区只移不删

若你发现可绕过以上任一项的构造，请务必按上述渠道报告。

## 路径注入守卫的写法约束 / Guard shape for path validation

`_local_path()`（[web/app.py](../web/app.py)）是 Web 层所有请求路径的唯一入口。
它的代码形态受一条硬约束限制，改之前请先读完本节：

**两个平台各自决定"根"，但必须共用同一条 `startswith(root)` 校验语句收口。**

CodeQL 的 `py/path-injection` 只认「`os.path.realpath()` 之后紧跟一个
`startswith(root)` 判定」这一种净化形态。v0.18.0 初版为了支持 Linux 把守卫拆成
按平台各写一套 `return`，语义完全等价（当时逐例比对过），但 CodeQL 认不出这是
守卫，把整条调用链重新报成注入——2026-11-26 那次 `35fa573` 一次性新增 7 条 high
警报就是这么来的。

因此：

- 新增平台分支时，只让分支决定 `root` 的值，**不要**在分支里提前 return。
- 改完跑一次本地 CodeQL 核对（见下），别只看单元测试——语义对了 CodeQL 仍可能报。
- 历史上这批警报按"本地单用户工具"的威胁模型 close 过（见
  [STATUS.md](STATUS.md) 工具链一节），但那不等于可以放任形态退化。

本地核对（需要 CodeQL CLI）：

```
codeql database create /tmp/db --language=python --source-root=.
codeql database analyze /tmp/db \
  codeql/python-queries:codeql-suites/python-code-scanning.qls \
  --format=sarif-latest --output=/tmp/res.sarif
```

`python-code-scanning.qls` 就是 GitHub default setup 用的那套查询；
比对 `/tmp/res.sarif` 里 `py/path-injection` 的条数应为 0。
