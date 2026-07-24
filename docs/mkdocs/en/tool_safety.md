# Tool Script Safety Guard

The Tool Script Safety Guard is an **opt-in** pre-execution static scanner for
Tool, Skill, and CodeExecutor payloads. It analyzes Python and Bash content
before execution and returns `allow`, `deny`, or `needs_human_review`.

Packages:

- `trpc_agent_sdk.safety` — implementation
- `trpc_agent_sdk.tools.safety` — official re-export

## Quick start

```python
from trpc_agent_sdk.safety import PolicyConfig, SafetyScanner, ScanInput

policy = PolicyConfig.from_yaml("examples/tool_safety/tool_safety_policy.yaml")
# or: policy = PolicyConfig.from_env()  # TOOL_SAFETY_POLICY_PATH
scanner = SafetyScanner(policy=policy)
report = scanner.scan(ScanInput(script="rm -rf /", language="bash"))
assert report.decision.value == "deny"
```

Opt-in on built-in tools / executors (default remains off):

```python
BashTool(enable_safety_guard=True, safety_policy_path="...")
UnsafeLocalCodeExecutor(enable_safety_guard=True)
```

Attach as a Tool Filter:

```python
from trpc_agent_sdk.tools.safety import ToolSafetyFilter, PolicyConfig

policy = PolicyConfig.from_yaml("examples/tool_safety/tool_safety_policy.yaml")
tool = BashTool(filters=[ToolSafetyFilter(policy=policy, audit_path="audit.jsonl")])
```

## Risk coverage

| Rule | Detects |
|---|---|
| R001 dangerous files | recursive delete, credential paths, system dirs, pathlib chains |
| R002 network egress | non-allowlisted hosts via curl/requests/httpx/Session/socket |
| R003 process/system | subprocess/os.system (with import aliases), getattr, eval, base64\|sh |
| R004 dependency install | pip/npm/apt and subprocess list forms |
| R005 resource abuse | infinite loops, fork bombs, long sleep, large writes |
| R006 secret leak | hardcoded secrets, env secret sinks, credential upload |

## Policy

Edit YAML to change allow-listed domains, forbidden paths, allowed commands,
decision thresholds, `block_on_review`, and `strict_command_allowlist` without
code changes.

## Integration points

- `ToolSafetyFilter` — BaseFilter pre-hook
- `wrap_tool` / `safety_wrapper` — generic wrappers
- `SafetyReviewedSkillRunner` — Skill path
- `SafetyGuardedCodeExecutor` / `safe_code_executor` — CodeExecutor path
- `scripts/tool_safety_check.py` — CLI / CI gate (exit 0/1/2)

## Observability

Every decision can write a JSONL audit record and set OpenTelemetry attributes:

- `tool.safety.decision`
- `tool.safety.risk_level`
- `tool.safety.rule_id`
- `tool.safety.scan_duration_ms`
- `tool.safety.blocked`

## Limits

Static analysis cannot catch every dynamic construction. Use this guard **with**
sandbox isolation (ContainerCodeExecutor / process limits). The guard is the
first line of defense; the sandbox is the last.
