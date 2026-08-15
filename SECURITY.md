# 安全政策 / Security Policy

## 支持版本 / Supported Versions

| Version / 版本 | Supported / 支持状态 |
|---|---|
| 0.10.x | ✅ 接收安全报告 receiving security reports |
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

## 安全相关的已知边界 / Known security boundaries

本项目处理的数据常来自不可信输入（他人打包的成品/APK/压缩包），
已内置的防护包括：

- RPA 索引反序列化白名单（防恶意 pickle 执行代码）
- 压缩包/封包条目路径净化（防 zip-slip 路径穿越）
- 引擎目录保护（renpy/、lib/、assets/x-renpy/ 绝不写）
- 隔离区只移不删

若你发现可绕过以上任一项的构造，请务必按上述渠道报告。
