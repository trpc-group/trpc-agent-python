# Tool Script Safety Guard

Pre-execution safety scanning for Python scripts and Bash commands in tRPC-Agent-Python tools.

## Overview

When tRPC-Agent tools (FunctionTool, MCPTool, BashTool, etc.) execute scripts or shell commands, the Safety Guard scans the content **before execution** for security risks and can block dangerous operations.

## Architecture

```
Script Input → ToolSafetyScanner → Pattern Rules (regex) + AST Rules (Python)
               → Policy Matcher → Decision (allow/deny/needs_human_review)
               → Audit Log (JSONL) + OTel Span Attributes
```

The scanner also integrates as a `BaseFilter` in the tool filter chain, so it runs automatically before every tool execution.

## Quick Start

### Filter Mode (automatic)

```python
from trpc_agent_sdk.tools.safety import ToolSafetyScanner, ToolSafetyFilter, SafetyAuditLogger
from trpc_agent_sdk.tools import FunctionTool

scanner = ToolSafetyScanner("path/to/tool_safety_policy.yaml")
audit = SafetyAuditLogger("tool_safety_audit.jsonl")

# Create filter and attach directly to a tool
filter_instance = ToolSafetyFilter(scanner=scanner, audit_logger=audit)
tool = FunctionTool(func=my_func, filters=[filter_instance])
```

### Standalone Mode

```python
from trpc_agent_sdk.tools.safety import ToolSafetyScanner

scanner = ToolSafetyScanner("tool_safety_policy.yaml")
report = await scanner.scan("rm -rf /home/user/data", tool_name="bash_tool")

if report.decision == Decision.DENY:
    print(f"Blocked: {report.findings[0].message}")
```

## Risk Categories

| Category | Detection | Example Triggers |
|----------|-----------|-----------------|
| Dangerous File Ops | Pattern + AST | `rm -rf /`, `os.remove()`, `~/.ssh` access |
| Network Access | Pattern | `curl`, `wget`, `requests.get()`, `socket.connect()` |
| System Commands | Pattern + AST | `subprocess.run()`, `os.system()`, `sudo`, shell pipes |
| Dependency Install | Pattern | `pip install`, `npm install`, `apt-get install` |
| Resource Abuse | Pattern | `while True:`, fork bombs, `for(;;)` |
| Sensitive Info Leak | Pattern + AST | Printing API keys/tokens/passwords to logs/output |

## Policy Configuration

Edit `tool_safety_policy.yaml` to control behavior without changing code:

- `whitelist.domains` — trusted domains allowed in network calls
- `whitelist.commands` — safe shell commands to always allow
- `blocklist.paths` — file paths that trigger alerts
- `rules[].enabled` — enable/disable individual rules
- `rules[].decision` — override per-rule decisions
- `max_script_size_bytes` — reject oversized scripts
- `max_scan_time_ms` — hard timeout for scanning

## Audit Logging

Events are written as JSONL to `tool_safety_audit.jsonl`:

```json
{"timestamp":"2026-07-10T12:00:00Z","tool_name":"bash_tool","decision":"deny","risk_level":"critical","rule_ids":["DANGEROUS_DELETE_001"],"scan_duration_ms":12.5,"sanitized":false,"intercepted":true,"script_hash":"a1b2c3..."}
```

## OpenTelemetry

When an active span exists, the filter sets these attributes:
- `tool.safety.decision`
- `tool.safety.risk_level`
- `tool.safety.rule_ids`
- `tool.safety.scan_duration_ms`

## Relationship to Other Components

| Component | Relationship |
|-----------|-------------|
| **CodeExecutor** | Safety Guard is pre-execution scanning. CodeExecutors provide runtime sandboxing. Both layers are complementary. Safety Guard does NOT replace sandbox isolation. |
| **Filter System** | ToolSafetyFilter plugs into BaseTool._run_filters() via the existing filter chain. |
| **Telemetry** | Decorates existing tool execution spans; does not create new spans or metrics. |
| **Callbacks** | Runs alongside callback filters in the chain. Should be registered first to block before callbacks execute. |

## Extending with New Rules

### Pattern Rule

```python
from trpc_agent_sdk.tools.safety._rules import PatternRule
from trpc_agent_sdk.tools.safety._types import RiskType, RiskLevel

my_rule = PatternRule(
    rule_id="MY_CUSTOM_001",
    risk_type=RiskType.SYSTEM_COMMAND,
    risk_level=RiskLevel.HIGH,
    message="Custom dangerous pattern detected",
    recommendation="Avoid this pattern",
    patterns=[r"evil_pattern\b"],
)
BUILTIN_PATTERN_RULES.append(my_rule)
```

### AST Rule

```python
import ast
from trpc_agent_sdk.tools.safety._rules import AstRule
from trpc_agent_sdk.tools.safety._types import RiskType, RiskLevel, RuleFinding

def check_my_pattern(tree: ast.AST) -> list[RuleFinding]:
    findings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ...:
            findings.append(RuleFinding(
                rule_id="MY_AST_001",
                risk_type=RiskType.DANGEROUS_FILE_OP,
                risk_level=RiskLevel.HIGH,
                evidence=f"Dangerous call at line {node.lineno}",
                message="Custom AST check triggered",
                recommendation="Remove this call",
            ))
    return findings

my_ast_rule = AstRule(
    rule_id="MY_AST_001",
    risk_type=RiskType.DANGEROUS_FILE_OP,
    risk_level=RiskLevel.HIGH,
    message="Custom AST check",
    recommendation="Remove this call",
    check=check_my_pattern,
)
BUILTIN_AST_RULES.append(my_ast_rule)
```

## Known Limitations

1. **Pattern bypass**: Obfuscated code (e.g., `__import__("os").system(...)`) can evade regex rules
2. **Bash is pattern-only**: No Bash AST parser exists; complex Bash with variable indirection may not be caught
3. **False positives**: Safety tutorials or documentation scripts mentioning dangerous keywords will be flagged
4. **No data flow analysis**: Syntax check only; a script reading `API_KEY` without outputting it is still flagged
5. **Not a sandbox**: This is static analysis; it cannot prevent runtime exploits, memory corruption, or novel attacks
6. **Whitelist fast path is conservative**: Any non-whitelisted element triggers full scan
