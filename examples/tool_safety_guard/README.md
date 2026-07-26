# Tool Script Safety Guard

该示例展示 Python/Bash 静态扫描、Tool Filter、CodeExecutor wrapper、策略、
报告、审计和 OpenTelemetry 接入。

## 交付物

- `README.md`：规则、接入、扩展方式和安全边界。
- `tool_safety_policy.yaml`：可修改的策略示例。
- `tool_safety_report.json`：结构化扫描报告示例。
- `tool_safety_audit.jsonl`：审计事件示例。
- `manifest.yaml` 与 `samples/`：12 个公开验收样本及预期决策。
- `real_agent.py`、`mcp_server.py` 与 `skills/`：真实 Agent 执行示例。

## CLI

```bash
python scripts/tool_safety_check.py \
  --file examples/tool_safety_guard/samples/danger_delete.py \
  --language python \
  --policy examples/tool_safety_guard/tool_safety_policy.yaml \
  --report tool_safety_report.json \
  --audit tool_safety_audit.jsonl
```

退出码：`0=allow`、`2=needs_human_review`、`3=deny`、`1=CLI/config error`。

## Tool Filter

```python
from trpc_agent_sdk.tools import BashTool
from trpc_agent_sdk.tools.safety import JsonlAuditSink
from trpc_agent_sdk.tools.safety import ToolSafetyFilter

audit = JsonlAuditSink("tool_safety_audit.jsonl")
safety_filter = ToolSafetyFilter.from_policy(
    "examples/tool_safety_guard/tool_safety_policy.yaml",
    audit,
)
tool = BashTool(cwd=".")
tool.add_one_filter(safety_filter)
```

Filter 在 `_run_async_impl()` 前扫描。`deny` 和 `needs_human_review` 不执行
handler；`allow` 把有效 timeout 注入 Tool 参数，并在 `_after` 阶段限制返回给
Agent 的 output 大小。安全 Filter 应放在所有参数改写 Filter 之后，后续 Filter
也不应扩展其已截断的输出。CodeExecutor wrapper 另外使用协作式 deadline。
已知 Tool 使用固定 adapter；未知 Tool 只要提供非空 `command`、`code` 或
`script` 字段，也会按 Bash/Python 保守扫描，避免自定义执行 Tool 静默绕过。

## CodeExecutor

```python
from trpc_agent_sdk.tools.safety import SafetyGuardedCodeExecutor
from trpc_agent_sdk.tools.safety import ToolScriptSafetyGuard

safe_executor = SafetyGuardedCodeExecutor(
    delegate=executor,
    guard=ToolScriptSafetyGuard.from_policy(
        "examples/tool_safety_guard/tool_safety_policy.yaml",
    ),
    audit_sink=audit,
)
```

wrapper 统一执行扫描、审计、阻断、wall-clock timeout 和返回 output 截断；
超时返回 `CodeExecutionResult(is_timed_out=True)`。

## 真实模型 Agent

`real_agent.py` 创建一个真正的 `LlmAgent`，同时接入：

- `BashTool`
- `SkillToolSet` 的 `skill_run`/`skill_exec`/`workspace_exec`
- 本地 stdio MCP Tool `execute_command`
- `SafetyGuardedCodeExecutor`

MCP 示例使用 argv-only 的 `create_subprocess_exec`，不提供 Shell 管道、重定向或
命令拼接语义；此类输入会在执行前进入人工审核。`mcp-review` 使用未加入命令
白名单的 `uname -a` 演示审核路径。

每个入口都提供 `allow`、`review`、`deny` 场景：

```bash
export TRPC_AGENT_API_KEY='<your-key>'
export TRPC_AGENT_BASE_URL='https://api.deepseek.com'
export TRPC_AGENT_MODEL_NAME='deepseek-v4-flash'

python examples/tool_safety_guard/real_agent.py --list-scenarios
python examples/tool_safety_guard/real_agent.py all
python examples/tool_safety_guard/real_agent.py tool-allow
python examples/tool_safety_guard/real_agent.py mcp-review
python examples/tool_safety_guard/real_agent.py skill-deny
python examples/tool_safety_guard/real_agent.py executor-allow
```

场景共 12 个，命名为
`{tool|mcp|skill|executor}-{allow|review|deny}`。终端输出模型实际发出的 `CALL`
和框架返回的 `RESULT`，audit 默认写入 `real_agent_audit.jsonl`。

MCP 子进程只继承运行所需的 `PATH`/Python/系统环境，不继承 API key、token 等
父进程密钥。

review/deny 使用演示目录内的有限副作用命令，但本示例仍包含真实本地执行器。
不要在生产主机运行；生产必须换成容器/沙箱执行器。密钥只通过环境变量传入，
禁止写入代码、策略、prompt 或 audit。

### 真实运行结果

使用 `deepseek-v4-flash` 实际运行四类入口后的结果：

| 入口 | allow | review | deny |
|---|---|---|---|
| `BashTool` | handler 执行，stdout=`tool-allow` | `PROC001/medium`，未执行 | `FILE001/critical`，未执行 |
| Skill `skill_run` | workspace 执行，stdout=`skill-allow` | `PROC001/medium`，未执行 | `FILE001/critical`，未执行 |
| MCP `execute_command` | MCP server 收到调用，stdout=`mcp-allow` | `PROC001/medium`，MCP server 未收到调用 | `FILE001/critical`，MCP server 未收到调用 |
| CodeExecutor | `Outcome.OUTCOME_OK`，输出 `executor-allow` | `PROC001/high`，delegate 未执行 | `FILE001/critical`，delegate 未执行 |

模型在 `skill-review` 的自然语言总结中曾错误描述为“没有阻断”，但实际
`RESULT` 是 `needs_human_review`，且 handler 未运行。这说明安全验收必须以
结构化 Tool result 和 audit 为准，不能信任模型对安全结果的二次转述。
真实模型验收依赖付费外部 API 且输出非确定，因此不进入默认 pytest；上述
`all` 命令是可重复的显式验收入口，默认测试继续覆盖确定性的 handler 未调用
断言。

## 扩展规则

在 `_python_rules.py`、`_bash_rules.py` 或 `_common_rules.py` 中增加小型规则，
使用 `_common_rules.py` 的 `RuleSpec` 和 `make_finding()`，并补充 rule id、decision、
evidence、recommendation 测试。策略字段由 Pydantic 严格校验。

## 安全边界

该机制是执行前静态检查，不是沙箱。动态拼接、反射、编码混淆、运行时下载、
符号链接、DNS 重绑定和未知解释器语义可能绕过规则。它也不能强制 CPU、内存、
PID、磁盘、网络或子进程内核输出配额。
不响应 Python 取消的第三方 handler 也可能延迟返回，必须由执行器或容器提供
进程级硬超时和清理。

生产环境仍需容器/沙箱、最小权限、只读挂载、网络白名单和运行时资源限制。
Filter 只保护明确挂载它的 Tool；必须清点所有 Tool、MCP Tool、Skill 和
CodeExecutor 执行入口。JSONL 仅为本地示例，多进程生产部署应使用集中审计。
POSIX 上 JSONL 文件强制为 `0600`；自定义 audit sink 通过 `emit_report` 串行
调用。
