# Tool Script Safety Guard 设计说明

## 目标与边界

Safety Guard 是执行前治理层。它对脚本、命令、参数、工作目录、环境变量名称和 tool 元数据做静态检查，输出 `allow`、`deny` 或 `needs_human_review`。它不能替代进程隔离、只读文件系统、最小权限、网络出口控制和运行时资源限制。

数据流如下：

```text
Tool / MCP Tool / Skill / CodeExecutor 请求
        |
        v
SafetyScanRequest（不保留环境变量值）
        |
        v
ToolSafetyScanner + ToolSafetyPolicy + SafetyRule[]
        |
        v
SafetyFinding[] --聚合--> SafetyReport
        |                         |
        v                         v
JSONL AuditEvent          Filter / Guard 执行决策
        |
        v
OpenTelemetry tool.safety.* 属性
```

扫描发生在真实 handler 或 executor delegate 之前。`deny` 必须阻断；`needs_human_review` 是否阻断由 `block_on_review` 决定。人工批准应由上层审批系统显式记录，不能把 review 自动降级为 allow。

## 接入示例

### Tool 与 MCP Tool Filter

Tool filter 从实际 tool 上下文获取名称，只接收参数中的脚本字段，不要求模型伪造 `tool_name`：

```python
from trpc_agent_sdk.tools import BashTool
from trpc_agent_sdk.tools.safety import JsonlAuditSink
from trpc_agent_sdk.tools.safety import ToolSafetyFilter, ToolSafetyGuard, ToolSafetyPolicy

policy = ToolSafetyPolicy.from_yaml("tool_safety_policy.yaml")
safety_guard = ToolSafetyGuard(policy, audit_sink=JsonlAuditSink("tool_safety_audit.jsonl"))
safety_filter = ToolSafetyFilter(safety_guard)
tool = BashTool()
tool.add_one_filter(safety_filter)
```

Safety Filter 带有最终授权标记，框架会让其他参数转换 Filter 和 tool callback 先运行，再在最靠近真实 handler 的位置扫描最终参数，避免下游原地修改已扫描内容。

Filter 会在扫描前合并可信的 tool 固定 override、默认 timeout 和默认 cwd。以示例策略的 `max_timeout_seconds: 120` 为例，`BashTool` 缺省的 300 秒 timeout 会被拒绝，调用方必须显式请求不超过策略的值。参数转换、scanner 或报告聚合异常会生成脱敏的 `SCAN-INPUT` / `SCAN-ERROR` deny 报告并记录一次审计事件，不会以普通异常形式绕过审计。

`StreamingProgressTool` 在启动用户 async generator 之前运行同一套有序 Filter 和 tool callback；被拒绝时只返回结构化阻断结果，generator 不会开始执行。最终授权只针对完整组装后的 tool call，不应对早期参数分片做放行判断。

MCP Tool 应在本地代理真正发出 MCP 调用前应用同一个 Filter。远端 MCP server 仍需独立鉴权、最小权限和审计，因为本地静态扫描无法证明远端实现的实际行为。

### Skill

`skill_run` 的 `command`、`cwd`、`env` 名称和 timeout 都应在 workspace runner 启动进程前进入 Filter：

```python
from trpc_agent_sdk.skills.tools import SkillRunTool
from trpc_agent_sdk.tools.safety import ToolSafetyFilter

skill_tool = SkillRunTool(repository=repository, filters=[ToolSafetyFilter(safety_guard)])
```

Skill 内容可能在扫描后被更新，因此还要固定 skill 版本或内容摘要，并在执行环境中限制挂载、网络和凭据。

### CodeExecutor wrapper

CodeExecutor 使用委托包装，逐个保留代码块语言，再聚合报告：

```python
from trpc_agent_sdk.code_executors import UnsafeLocalCodeExecutor
from trpc_agent_sdk.tools.safety import SafetyGuardedCodeExecutor

delegate = UnsafeLocalCodeExecutor(timeout=30)
executor = SafetyGuardedCodeExecutor(inner=delegate, guard=safety_guard)
```

包装器需要镜像 delegate 的 `stateful`、workspace runtime、delimiter 和重试配置，阻断时不得调用 delegate。

## 规则与策略

Python 使用 AST 提取调用、常量路径和数据流信号；Bash 使用不执行命令的 token/语法模式扫描。每个 finding 至少包含：

- `rule_id` 和风险分类；
- `risk_level` 与局部决策；
- 已脱敏、长度受限的 `evidence`；
- 可执行的 `recommendation`；
- 可选行列位置和不含秘密值的 metadata。

自定义规则通过 scanner 的 `rules` 参数注入，不修改内置 scanner：

```python
from trpc_agent_sdk.tools.safety import SafetyDecision, SafetyFinding
from trpc_agent_sdk.tools.safety import RiskCategory, RiskLevel, ToolSafetyScanner

class DenyInternalBinaryRule:
    rule_id = "CUSTOM-INTERNAL-BINARY"

    def scan(self, context, policy):
        if "/internal/bin/" not in context.request.script:
            return []
        return [SafetyFinding(
            rule_id=self.rule_id,
            category=RiskCategory.PROCESS_EXECUTION,
            risk_level=RiskLevel.HIGH,
            decision=SafetyDecision.DENY,
            evidence="/internal/bin/<redacted>",
            recommendation="Use an explicitly approved command.",
        )]

scanner = ToolSafetyScanner(policy, rules=[DenyInternalBinaryRule()])
```

自定义规则必须是纯静态、确定性且无副作用；异常由 scanner 按 policy 的 fail-closed 行为处理。

## 审计与监控

审计事件至少包含 `tool_name`、`decision`、`risk_level`、`rule_ids`、扫描耗时、`redacted`、`blocked`、人工批准状态、脚本 SHA-256 和策略版本。不得写入脚本文本、环境变量值、Authorization header 或私钥正文。

当前 span 应设置：

- `tool.safety.decision`
- `tool.safety.risk_level`
- `tool.safety.rule_id`
- `tool.safety.rule_ids`
- `tool.safety.duration_ms`
- `tool.safety.redacted`
- `tool.safety.blocked`

策略版本和脚本 SHA-256 只写入审计事件，不写入当前 span，以控制 telemetry 基数。

监控系统可以按 deny/review 比例、rule id、tool name 和扫描失败率告警，但不能把 telemetry 成功视为安全放行条件。

## 已知限制

- **误报**：Bash 的复杂引用、合法管道、安全的 subprocess 和测试夹具可能触发 review；通过窄化白名单或单条 rule action 调整，不应关闭整个 guard。
- **漏报**：编码、压缩、别名、间接导入、反射、动态属性和多阶段下载执行可能绕过静态模式。
- **动态代码**：`eval`、`exec`、运行时拼接 URL/路径和下载后的脚本无法在首次扫描时完全解析，应阻断或人工复核，并在下一执行边界重新扫描。
- **TOCTOU**：扫描后文件、Skill、cwd、符号链接或策略可能变化。执行端应校验内容摘要，固定版本，并尽量在隔离 workspace 内完成扫描和执行。
- **MCP**：本地只能分析请求参数，无法验证远端 tool 是否执行了额外命令、访问其他数据或正确实施资源限制。
- **Streaming**：分片参数在结束前可能不是完整语法。只能在完整 tool call 组装后做最终授权，不能因早期分片看似安全而提前执行。
- **资源限制**：静态规则只能识别明显循环、sleep、fork bomb 和大写入信号；CPU、内存、进程数、磁盘、输出和墙钟时间必须由 sandbox/runtime 强制限制。
- **运行时注入**：Guard 会检查调用参数、固定 tool override 和可见默认值，但 workspace/repository 在 handler 内部追加的可信环境或文件仍需由运行时白名单、固定配置和 sandbox 约束。

因此生产部署应组合：Safety Guard + Container/Cube sandbox + 最小凭据 + 出网白名单 + 资源限制 + 不可篡改审计。
