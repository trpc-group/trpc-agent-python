---
name: code-review
description: 基于规则的代码审查技能，支持安全检测、异步错误分析、资源泄漏检测、数据库连接分析和敏感信息检测。
---

# Code Review Skill

对代码变更进行自动化审查，识别潜在的安全风险、异步错误、资源泄漏、数据库连接问题、敏感信息泄漏等。

## Rules

本技能包含 5 类风险规则文档：

| 规则 | 文件 | 覆盖范围 |
|------|------|---------|
| 安全风险 | `rules/security.md` | SQL 注入、命令注入、路径遍历、XSS、动态代码执行 |
| 异步错误 | `rules/async_errors.md` | 未处理的异步异常、协程泄漏、事件循环阻塞 |
| 资源泄漏 | `rules/resource_leak.md` | 文件句柄未关闭、连接未释放、内存泄漏模式 |
| 数据库连接 | `rules/db_connection.md` | 连接未关闭、事务未提交/回滚、连接池耗尽 |
| 敏感信息检测 | `rules/secret_detection.md` | 硬编码 API Key、Token、密码、证书、数据库连接串 |

## Scripts

沙箱中可执行的检查脚本：

| 脚本 | 用途 | 输入 | 输出 |
|------|------|------|------|
| `scripts/parse_diff.py` | 解析 unified diff | `<diff_file> <output_file>` | `out/parsed_diff.json` |
| `scripts/run_static_check.py` | 运行静态分析 | `<file> <rules> <output_file>` | `out/findings.json` |
| `scripts/detect_secrets.py` | 敏感信息检测 | `<file> <output_file>` | `out/secrets.json` |
| `scripts/run_tests.py` | 运行单元测试 | `<test_path> <output_file>` | `out/test_results.json` |

## Output Files

- `out/parsed_diff.json` — 结构化变更信息
- `out/findings.json` — 静态检查结果
- `out/secrets.json` — 敏感信息检测结果
- `out/test_results.json` — 测试执行结果

## Usage

```python
from trpc_agent_sdk.skills import SkillToolSet

skill_set = SkillToolSet("skills/code-review")
await skill_set.skill_load("code-review")  # 加载规则文档
await skill_set.skill_run("scripts/parse_diff.py", args=[...])  # 执行脚本
```