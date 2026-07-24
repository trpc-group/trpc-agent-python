---
name: code-review
description: >-
  自动代码评审 Skill,加载 6 类规则与脚本,在沙箱中执行检查并产出结构化 findings。
version: 1.0.0
entry: scripts/run_checks.py
rules:
  - rules/security.md
  - rules/async_errors.md
  - rules/resource_leak.md
  - rules/missing_tests.md
  - rules/sensitive_info.md
  - rules/db_lifecycle.md
sandbox:
  default_runtime: container
  fallback: local
  timeout_s: 30
  max_output_bytes: 1048576
  env_whitelist: [PATH, HOME, LANG]
---

# code-review Skill

把 `ChangeSet`(unified diff 解析产物)送进沙箱,按 6 类规则做静态检查,
产出结构化 finding,再由编排层去重、分流、落库。

## 入口

- `scripts/parse_diff.py` —— unified diff → `ChangeSet`(本地 L1 执行)
- `scripts/run_checks.py` —— `ChangeSet` + `RuleSet` → 原始诊断(沙箱 L4 执行)
- `scripts/dedupe.py` —— 原始诊断 → 分流后 findings(本地 L5)
- `scripts/mask_secrets.py` —— 输出落地前脱敏(沙箱输出落地前)

> Phase 1 只落地 `parse_diff.py` 与本契约;`run_checks.py` / `dedupe.py` /
> `mask_secrets.py` 由 P2/P3/P4 实现。`skill_load` 通过扫描 `scripts/*.py`
> 收集当前已存在的脚本清单。

## 规则清单

| 规则 | 覆盖问题 | 默认 severity |
|------|----------|---------------|
| `rules/security.md` | SQL 注入、命令注入、硬编码密钥、不安全反序列化 | critical / high |
| `rules/async_errors.md` | 未 await 协程、未处理 rejection、async 资源泄漏 | high / medium |
| `rules/resource_leak.md` | 未关闭文件/连接、try 无 finally | high / medium |
| `rules/missing_tests.md` | 新增公开函数无对应测试 | low |
| `rules/sensitive_info.md` | 明文 API key / token / password | critical |
| `rules/db_lifecycle.md` | 连接未关闭、事务未提交/回滚、连接池泄漏 | high / medium |

## 沙箱策略

- 默认 `container`,本地仅作 dry-run / 开发 fallback。
- 超时 30s,输出上限 1MB,env 白名单 `PATH`/`HOME`/`LANG`。
- 五项安全边界由 `sandbox/policy.py`(P3)强制套用,不可绕过。
