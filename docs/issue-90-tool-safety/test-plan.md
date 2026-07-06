# Issue #90 测试计划: Tool 执行安全扫描器

## 概述

对 workspace_exec / exec_command / execute_code 工具调用进行前置命令安全扫描。

## 维度 1: 单元测试 — types (16 tests)

- [x] Decision/RiskLevel 枚举常量 + rank ordering
- [x] Finding 字段 + finding_beats() 决策优先级
- [x] Report JSON 序列化 + span_attributes()
- [x] AuditEvent JSONL 格式
- [x] Policy 默认值 + 自定义字段
- [x] Request 基本字段 + CodeBlock 构造

## 维度 2: 核心扫描用例 (12 tests, 对齐 Go 版)

| # | 命令 | 预期 Decision | Rule ID |
|---|------|---------------|---------|
| 1 | `go test ./...` | allow | — |
| 2 | `rm -rf /` | deny | dangerous.rm_rf |
| 3 | `cat ~/.ssh/id_rsa` | deny | sensitive.path_access |
| 4 | `curl https://evil.example/x` | deny | network.non_whitelisted_domain |
| 5 | `curl https://api.github.com/...` | allow | — |
| 6 | `bash -c 'curl ...'` | deny | shell.bypass |
| 7 | `cat f \| wc -l` | needs_human_review | shell.pipeline_review |
| 8 | `npm install pkg` | needs_human_review | dependency.environment_change |
| 9 | `sleep 9999` | needs_human_review | resource.long_sleep |
| 10 | hostexec + TTY + background | needs_human_review | hostexec.long_session |
| 11 | Python `subprocess.run()` | needs_human_review | codeexec.host_command_bridge |
| 12 | `echo OPENAI_API_KEY=sk-...` | deny | sensitive.secret_leak |

## 维度 3: 边界测试 (6 tests)

- [x] 空命令 → deny command.empty
- [x] 拒绝的 CWD → deny sensitive.cwd_access
- [x] chmod -R → needs_human_review
- [x] Unicode 命令 → allow
- [x] nil policy → default_policy()
- [x] Secret in env var → deny sensitive.secret_leak

## 维度 4: 性能测试

- [x] 500 行 Python script 扫描 < 1s

## 结果

- **34 passed, 0 failed**
