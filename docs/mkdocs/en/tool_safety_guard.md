# Tool Script Safety Guard

A pluggable safety layer that scans Python scripts and Bash commands **before**
they are executed by a Tool, MCP Tool, Skill or CodeExecutor.

## Overview

tRPC-Agent's Tool, MCP Tool, Skill and CodeExecutor let agents execute scripts,
call external commands, read/write files and access the network. The Safety Guard
provides a **pre-execution static scan** that produces an `allow` / `deny` /
`needs_human_review` decision based on configurable rules. It is **not** a sandbox
replacement — it complements sandbox isolation with fast, deterministic risk assessment.

## Quick Start

```python
from trpc_agent_sdk.tools.safety import SafetyGuard, ToolSafetyFilter
from trpc_agent_sdk.tools import BashTool

guard = SafetyGuard.default()
report = guard.scan("import os; os.system('rm -rf /')", tool_name="BashTool")
print(report.decision)  # Decision.DENY

# Attach to a tool via Filter
bash = BashTool(filters=[ToolSafetyFilter(guard)])
```

## Risk Categories & Built-in Rules

The guard detects **6 categories** of risk across Python (AST-based) and Bash
(regex/token-based) scanners:

| Category | Example | Python Rule | Bash Rule |
| --- | --- | --- | --- |
| `dangerous_file_ops` | `rm -rf /`, `open('.env')`, `cat ~/.ssh/id_rsa` | `PY-DANGEROUS-FILE-OPS` (deny) | `BASH-DANGEROUS-FILE-OPS` (deny/critical) |
| `network_egress` | `requests.get('http://evil.com')`, `curl` non-whitelisted | `PY-NETWORK-EGRESS` (deny/high) | `BASH-NETWORK-EGRESS` (deny/high) |
| `process_system` | `os.system`, `subprocess` shell=True, `sudo` | `PY-PROCESS-SYSTEM` (tiered) | `BASH-PROCESS-SYSTEM` (deny/critical) |
| `dependency_install` | `pip install`, `npm install` | `PY-DEPENDENCY-INSTALL` (deny) | `BASH-DEPENDENCY-INSTALL` (deny) |
| `resource_abuse` | `while True`, fork bomb, huge writes | `PY-RESOURCE-ABUSE` (deny) | `BASH-RESOURCE-ABUSE` (deny/critical) |
| `secret_leak` | `api_key = 'sk-...'` in output | `PY-SECRET-LEAK` (deny) | `BASH-SECRET-LEAK` (deny/critical) |

Bash also has `BASH-SHELL-INJECTION` for `$(...)` and backtick substitution.

**Decisions**: `allow` (no risk) / `deny` (high risk, blocked) / `needs_human_review`
(suspicious, human must approve). Worst finding wins.

**Process system tiering**: sudo/su → CRITICAL deny; shell=True/os.system/string arg →
HIGH deny; list arg → MEDIUM needs_human_review.

## Policy Configuration

```yaml
allowed_domains:          # Network egress whitelist
  - localhost
  - pypi.org
allowed_commands:         # Bash command whitelist
  - ls
  - git
forbidden_paths:          # Blocked file paths
  - "~/.ssh"
  - ".env"
max_timeout_seconds: 300
max_output_size_mb: 50
secret_patterns:          # Custom secret detection regex
  - '(?i)(api[_-]?key)\s*[=:]\s*["'']?[A-Za-z0-9_\-]{16,}["'']?'
rules:                    # Per-rule overrides (no code change needed)
  PY-NETWORK-EGRESS:
    enabled: false
    decision: allow
```

```python
guard = SafetyGuard.from_yaml("path/to/tool_safety_policy.yaml")
```

## Integration

### Filter

`ToolSafetyFilter` plugs into the Tool execution pipeline `_before` phase.
If the scan denies the script, it sets `rsp.is_continue = False` to short-circuit.

```python
guard = SafetyGuard.default()
bash = BashTool(filters=[ToolSafetyFilter(guard)])
```

### Audit Logging

```python
from trpc_agent_sdk.tools.safety import AuditLogger
guard = SafetyGuard.default(audit_logger=AuditLogger(path="audit.jsonl"))
# Each scan writes a JSONL event: {tool_name, decision, risk_level, rule_id, ...}
```

### OpenTelemetry

Span attributes: `tool.safety.decision`, `tool.safety.risk_level`, `tool.safety.rule_ids`,
`tool.safety.scan_duration_ms`, `tool.safety.sanitized`, `tool.safety.blocked`.

### Extending Rules

```python
from trpc_agent_sdk.tools.safety import Rule, ScanContext, Finding, RiskCategory, Decision

class CustomRule(Rule):
    rule_id = "CUSTOM-NO-SLEEP"
    category = RiskCategory.RESOURCE_ABUSE
    default_decision = Decision.NEEDS_HUMAN_REVIEW
    applies_to = (ScriptType.PYTHON,)

    def check(self, ctx: ScanContext) -> list[Finding]:
        # Use ctx.cached_tree (pre-parsed AST) for performance
        ...

global_rule_registry.register(CustomRule())
```

## Relationship to Other Components

- **Sandbox / CodeExecutor**: The guard is a static pre-scan, not a runtime sandbox.
  It cannot catch dynamically generated code, obfuscated commands, or runtime resource
  exhaustion. Use the guard to reject obvious threats before they reach the sandbox;
  use the sandbox to contain anything that slips through.
- **Filter System**: Integrates as a `BaseFilter` with `FilterType.TOOL`, running in
  the `_before` phase before `_run_async_impl`.
- **Telemetry**: Safety decisions are emitted as OpenTelemetry span attributes for
  distributed tracing and monitoring.

## Known Limitations

1. **Obfuscation**: Base64-encoded commands, variable indirection, and alias tricks
   can bypass static scanning.
2. **Bash parsing**: Complex quoting, here-docs, and process substitutions may not be
   fully analyzed (line-level regex + `shlex`).
3. **False positives**: Legitimate `subprocess.run()` or `curl` may be flagged — use
   the policy file to whitelist or disable rules.
4. **No runtime protection**: Cannot detect runtime-downloaded code, execution-time
   memory exhaustion, or post-execution side effects.
5. **Secret detection is heuristic**: Regex patterns match common formats but not all
   possible encodings.
