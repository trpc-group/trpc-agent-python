# Tool 脚本安全扫描（Tool Script Safety Guard）

Tool Script Safety Guard 是面向 Tool / Skill / CodeExecutor 的**可选**执行前静态扫描器。
它对 Python / Bash 内容做策略判定，输出 `allow` / `deny` / `needs_human_review`。

包路径：`trpc_agent_sdk.safety`

## 快速开始

```python
from trpc_agent_sdk.safety import PolicyConfig, SafetyScanner, ScanInput

policy = PolicyConfig.from_yaml("examples/tool_safety/tool_safety_policy.yaml")
scanner = SafetyScanner(policy=policy)
report = scanner.scan(ScanInput(script="rm -rf /", language="bash"))
assert report.decision.value == "deny"
```

作为 Tool Filter 接入：

```python
from trpc_agent_sdk.safety import ToolSafetyFilter, PolicyConfig

policy = PolicyConfig.from_yaml("examples/tool_safety/tool_safety_policy.yaml")
tool = BashTool(filters=[ToolSafetyFilter(policy=policy, audit_path="audit.jsonl")])
```

## 风险覆盖

| 规则 | 检测内容 |
|---|---|
| R001 危险文件 | 递归删除、凭据路径、系统目录、pathlib 链 |
| R002 网络外连 | 非白名单域名（curl/requests/httpx/Session/socket） |
| R003 进程命令 | subprocess/os.system（含 import 别名）、getattr、eval、base64\|sh |
| R004 依赖安装 | pip/npm/apt 及 subprocess list 形态 |
| R005 资源滥用 | 无限循环、fork bomb、长 sleep、大写入 |
| R006 敏感泄漏 | 硬编码密钥、环境变量密钥 sink、凭据上传 |

## 策略配置

通过 YAML 调整白名单域名、禁止路径、允许命令、决策阈值、`block_on_review`、
`strict_command_allowlist`，无需改代码。

## 接入方式

- `ToolSafetyFilter` — BaseFilter 前置钩子
- `wrap_tool` / `safety_wrapper` — 通用包装
- `SafetyReviewedSkillRunner` — Skill 路径
- `SafetyGuardedCodeExecutor` / `safe_code_executor` — CodeExecutor 路径
- `scripts/tool_safety_check.py` — CLI / CI（退出码 0/1/2）

## 可观测性

每次决策可写 JSONL 审计，并在启用 OpenTelemetry 时设置：

- `tool.safety.decision`
- `tool.safety.risk_level`
- `tool.safety.rule_id`
- `tool.safety.scan_duration_ms`
- `tool.safety.blocked`

## 限制

静态分析无法覆盖全部动态构造。请与沙箱隔离（ContainerCodeExecutor / 进程限制）
一起使用：本工具是第一道防线，沙箱是最后一道防线。
