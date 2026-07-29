# Tool 脚本安全检测

一个可插拔的安全层，在 Tool、MCP Tool、Skill 或 CodeExecutor **执行之前**扫描 Python 脚本和 Bash 命令。

## 概览

tRPC-Agent 的 Tool、MCP Tool、Skill 和 CodeExecutor 允许 Agent 执行脚本、调用外部命令、读写文件和访问网络。安全检测**执行前静态扫描**，基于可配置规则产生 `allow` / `deny` / `needs_human_review` 决策，**不能**作为沙箱替代品，而是作为沙箱隔离的补充，提供快速、确定性的风险评估。

## 快速开始

```python
from trpc_agent_sdk.tools.safety import SafetyGuard, ToolSafetyFilter
from trpc_agent_sdk.tools import BashTool

guard = SafetyGuard.default()
report = guard.scan("import os; os.system('rm -rf /')", tool_name="BashTool")
print(report.decision)  # Decision.DENY

# 通过 Filter 挂载到 Tool
bash = BashTool(filters=[ToolSafetyFilter(guard)])
```

## 风险类别与内置规则

安全卫士检测 **6 类**风险，覆盖 Python（基于 AST）和 Bash（基于正则/token）扫描器：

| 类别 | 示例 | Python 规则 | Bash 规则 |
| --- | --- | --- | --- |
| `dangerous_file_ops` | `rm -rf /`, `open('.env')`, `cat ~/.ssh/id_rsa` | `PY-DANGEROUS-FILE-OPS` (deny) | `BASH-DANGEROUS-FILE-OPS` (deny/critical) |
| `network_egress` | `requests.get('http://evil.com')`, `curl` 非白名单 | `PY-NETWORK-EGRESS` (deny/high) | `BASH-NETWORK-EGRESS` (deny/high) |
| `process_system` | `os.system`, `subprocess` shell=True, `sudo` | `PY-PROCESS-SYSTEM` (分级) | `BASH-PROCESS-SYSTEM` (deny/critical) |
| `dependency_install` | `pip install`, `npm install` | `PY-DEPENDENCY-INSTALL` (deny) | `BASH-DEPENDENCY-INSTALL` (deny) |
| `resource_abuse` | `while True`, fork 炸弹, 大文件写入 | `PY-RESOURCE-ABUSE` (deny) | `BASH-RESOURCE-ABUSE` (deny/critical) |
| `secret_leak` | `api_key = 'sk-...'` 出现在输出中 | `PY-SECRET-LEAK` (deny) | `BASH-SECRET-LEAK` (deny/critical) |

Bash 还有 `BASH-SHELL-INJECTION` 检测 `$(...)` 和反引号命令替换，以及 `BASH-COMMAND-WHITELIST`（可选命令白名单，默认禁用）。

**决策**：`allow`（无风险）/ `deny`（高风险，拦截）/ `needs_human_review`（可疑，需人工审核）。最严重的发现优先。

**进程/系统命令分级**：sudo/su → CRITICAL deny；shell=True / os.system / 字符串参数 → HIGH deny；列表参数 → MEDIUM needs_human_review。

## 策略配置

```yaml
allowed_domains:          # 网络外连白名单
  - localhost
  - pypi.org
forbidden_paths:          # 禁止访问的文件路径
  - "~/.ssh"
  - ".env"
allowed_commands:         # Bash 命令白名单（留空=禁用检查）
  - ls
  - cat
  - echo
max_timeout_seconds: 300
max_output_size_mb: 50
secret_patterns:          # 自定义密钥检测正则
  - '(?i)(api[_-]?key)\s*[=:]\s*["'']?[A-Za-z0-9_\-]{16,}["'']?'
rules:                    # 按规则覆盖（无需改代码）
  PY-NETWORK-EGRESS:
    enabled: false
    decision: allow
```

```python
guard = SafetyGuard.from_yaml("path/to/tool_safety_policy.yaml")
```

## 集成

### Filter

`ToolSafetyFilter` 插入 Tool 执行管道的 `_before` 阶段。如果扫描拒绝脚本，设置 `rsp.is_continue = False` 短路管道。

```python
guard = SafetyGuard.default()
bash = BashTool(filters=[ToolSafetyFilter(guard)])
```

### 审计日志

```python
from trpc_agent_sdk.tools.safety import AuditLogger
guard = SafetyGuard.default(audit_logger=AuditLogger(path="audit.jsonl"))
# 每次扫描写入一条 JSONL 事件：{tool_name, decision, risk_level, rule_id, ...}
```

### OpenTelemetry

Span 属性：`tool.safety.decision`、`tool.safety.risk_level`、`tool.safety.rule_ids`、`tool.safety.scan_duration_ms`、`tool.safety.sanitized`、`tool.safety.blocked`。

### 扩展规则

```python
from trpc_agent_sdk.tools.safety import Rule, ScanContext, Finding, RiskCategory, Decision

class CustomRule(Rule):
    rule_id = "CUSTOM-NO-SLEEP"
    category = RiskCategory.RESOURCE_ABUSE
    default_decision = Decision.NEEDS_HUMAN_REVIEW
    applies_to = (ScriptType.PYTHON,)

    def check(self, ctx: ScanContext) -> list[Finding]:
        # 使用 ctx.cached_tree（预解析 AST）提升性能
        ...

global_rule_registry.register(CustomRule())
```

## 与其他组件的关系

- **沙箱 / CodeExecutor**：是静态预扫描，不是运行时沙箱。无法捕获动态生成的代码、混淆命令或运行时资源耗尽。用安全检测在脚本进入沙箱前拒绝明显威胁，用沙箱控制漏网之鱼。
- **Filter 系统**：作为 `BaseFilter`（`FilterType.TOOL`）集成，在 `_before` 阶段、`_run_async_impl` 之前运行。
- **Telemetry**：安全决策作为 OpenTelemetry span 属性输出，用于分布式追踪和监控。

## 已知限制

1. **混淆**：Base64 编码命令、变量间接引用、别名技巧可能绕过静态扫描。
2. **Bash 解析**：复杂引号、here-doc、进程替换可能无法完全分析（行级正则 + `shlex`）。
3. **误报**：可信场景中合法使用 `subprocess.run()` 或 `curl` 可能被标记——通过策略文件白名单或禁用规则。
4. **无运行时保护**：无法检测运行时下载执行的代码、执行期间内存耗尽、执行后的副作用。
5. **密钥检测是启发式的**：正则模式匹配常见格式，但无法覆盖所有可能的编码。
