# Tool Script Safety Guard — Design

## Goals

Provide an **opt-in** pre-execution static scanner for Tool / Skill / CodeExecutor
payloads so that dangerous Python/Bash scripts can be denied or escalated to
human review before they run.

## Non-goals

- Not a sandbox. Does not limit CPU, memory, filesystem, or network at runtime.
- Not a perfect static analyzer. Dynamic construction can still evade rules.
- Does not change default Tool/CodeExecutor behavior unless explicitly enabled.

## Architecture

```
request args (command/code/script)
        │
        ▼
  ToolSafetyFilter / wrapper / enable_safety_guard
        │
        ▼
  SafetyScanner
    ├─ language normalize
    ├─ rule pipeline (R001–R007 + custom)
    ├─ inline payload rescan (python -c / bash -c)
    └─ aggregate → Decision
        │
        ├─ DENY → block + audit + OTel
        ├─ NEEDS_HUMAN_REVIEW → warn or block (policy.block_on_review)
        └─ ALLOW → continue execution
```

## Package layout

| Path | Role |
|---|---|
| `trpc_agent_sdk/safety/` | Implementation (light import surface) |
| `trpc_agent_sdk/tools/safety/` | Official re-export entry |
| `scripts/tool_safety_check.py` | CLI / CI gate |
| `scripts/tool_safety_eval.py` | Detection / FP rate eval |
| `examples/tool_safety/` | Policy, samples, fixtures, docs |

## Risk domains

| Domain | Rule IDs |
|---|---|
| Dangerous files | R001 |
| Network egress | R002 |
| Process / system | R003 |
| Dependency install | R004 |
| Resource abuse | R005 |
| Secret leak | R006 |
| Dynamic code execution | R007 |

## Opt-in integration

```python
# Filter
BashTool(enable_safety_guard=True, safety_policy_path="...")

# Code executor
UnsafeLocalCodeExecutor(enable_safety_guard=True)

# Explicit filter
tool = BashTool(filters=[ToolSafetyFilter(policy=policy)])
```

Defaults remain **disabled**. Enabling is explicit.

## Policy

YAML controls domains, paths, commands, thresholds, `block_on_review`,
`strict_command_allowlist`, and `strict_policy`. Also loadable via
`TOOL_SAFETY_POLICY_PATH` / `PolicyConfig.from_env()`.

## Why this cannot replace a sandbox

| | Safety Guard | Sandbox |
|---|---|---|
| When | Before run | During run |
| Strength | Intent / pattern policy | Hard isolation |
| Failure mode | Miss dynamic payload | Still contained |

Use both: guard first, sandbox always for untrusted code.

## Extension

```python
from trpc_agent_sdk.safety import register_rule, SafetyRule

@register_rule
class MyRule(SafetyRule):
    rule_id = "CUSTOM_001"
    ...
```
