# Issue #90 设计说明: Tool Safety Scanner

## 架构

```
trpc_agent_sdk/tools/safety/
├── __init__.py        — 公开 API
├── _types.py          — Decision/RiskLevel/Policy/Request/Report/Finding/AuditEvent
├── _policy.py         — default_policy() + load_policy(YAML/JSON)
├── _shell_parse.py    — 命令名/管道/URL/Shell绕过 检测（纯 Python，不调 subprocess）
├── _redactor.py       — Secret 脱敏引擎（API key/token/password 自动替换）
├── _scanner.py        — 核心扫描引擎 scan(request, policy) → Report
└── _permission.py     — ToolSafetyFilter（FilterABC 子类）

tests/tools/safety/
├── test_types.py      — 16 类型测试
└── test_scanner.py    — 18 扫描测试（12 核心 + 6 边界）
```

## 设计决策

1. **对齐 Go 版** — 类型、rule_id、扫描流程均对齐 `trpc-agent-go/tool/safety/`
2. **纯 Python 静态分析** — shell 解析不调用 subprocess，regex 检测
3. **Filter 集成** — 通过 `FilterABC._before()` 在工具执行前拦截，支持 filter chain
4. **Secret 脱敏** — 扫描结束后对所有 report 字段做 redact，避免日志泄露
5. **策略分层** — `DefaultPolicy()` 提供保守默认值，`LoadPolicy()` 支持 YAML/JSON 文件覆盖
6. **Finding 优先级** — finding_beats() 按 decision 优先 > risk_level 优先，worst finding 驱动最终 Report

## 风险类别（6 类）

| 类别 | rule_id 前缀 | 示例 |
|------|-------------|------|
| 危险命令 | dangerous.* | rm -rf, chmod -R |
| 敏感信息 | sensitive.* | path_access, secret_leak, cwd_access |
| 网络外连 | network.* | non_whitelisted_domain |
| Shell 绕过 | shell.* | bypass, pipeline_review |
| 资源滥用 | resource.* | long_sleep, infinite_loop, timeout_exceeded |
| 依赖变更 | dependency.* | environment_change |

## 依赖

- `PyYAML` (已有) — policy YAML 解析
- `trpc_agent_sdk.abc.FilterABC` — filter 基类
- `re` / `urllib.parse` — shell 解析
