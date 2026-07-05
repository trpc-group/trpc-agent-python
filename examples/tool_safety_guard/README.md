# Tool Script Safety Guard

This example shows how to scan Python and Bash tool scripts before execution. It uses a policy file, emits structured reports, appends JSONL audit events, and can be attached as a Tool Filter or CodeExecutor wrapper.

## Run The Samples

```bash
python scripts/tool_safety_check.py examples/tool_safety_guard/samples/04_network_external.py \
  --policy examples/tool_safety_guard/tool_safety_policy.yaml \
  --report-out examples/tool_safety_guard/out/report.json \
  --audit-out examples/tool_safety_guard/out/audit.jsonl
```

To scan every sample:

```bash
for f in examples/tool_safety_guard/samples/*; do
  lang=python
  case "$f" in *.sh) lang=bash ;; esac
  python scripts/tool_safety_check.py "$f" --language "$lang" \
    --policy examples/tool_safety_guard/tool_safety_policy.yaml || true
done
```

## Policy

`tool_safety_policy.yaml` controls:

- `allowed_domains`: domains that network rules may allow without code changes.
- `allowed_commands`: commands permitted in command argument scans.
- `denied_paths`: sensitive paths such as `.env`, `~/.ssh`, cloud credentials, and private keys.
- `system_write_paths`: protected filesystem roots.
- `max_timeout_seconds`, `max_output_bytes`, `max_sleep_seconds`, `max_loop_iterations`, and write limits.
- `deny_risk_level`, `review_risk_level`, and `block_on_review`.

Changing the YAML is enough to alter domain allowlists, denied paths, and allowed commands.

## Decisions And Reports

The scanner returns:

- `allow`: no configured rule found a material risk.
- `needs_human_review`: medium risk or uncertain behavior, for example dynamic network targets.
- `deny`: high or critical risk such as credential reads, recursive deletion, non-allowlisted network egress, dependency installation, or secret leakage.

Every finding includes `risk_type`, `rule_id`, `evidence`, `recommendation`, line information when available, and whether evidence was redacted. Reports also include OpenTelemetry-ready attributes such as `tool.safety.decision`, `tool.safety.risk_level`, and `tool.safety.rule_id`.

## Filter Integration

Attach the registered Tool Filter to script-like tools:

```python
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools.safety import ToolSafetyFilter  # registers "tool_safety_guard"

tool = FunctionTool(run_script, filters_name=["tool_safety_guard"])
```

The filter scans common argument shapes: `command`, `script`, `code`, `source`, and `code_blocks`. For `deny`, and for `needs_human_review` when `block_on_review=true`, it returns a structured `TOOL_SAFETY_GUARD_BLOCKED` response before the tool body runs.

## CodeExecutor Wrapper

```python
from trpc_agent_sdk.code_executors import UnsafeLocalCodeExecutor
from trpc_agent_sdk.tools.safety import SafetyGuardedCodeExecutor
from trpc_agent_sdk.tools.safety import ToolSafetyGuard
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy

policy = ToolSafetyPolicy.load("examples/tool_safety_guard/tool_safety_policy.yaml")
executor = SafetyGuardedCodeExecutor(
    delegate=UnsafeLocalCodeExecutor(timeout=10),
    guard=ToolSafetyGuard(policy=policy),
)
```

## Relationship To Sandbox, Filter, Telemetry, And CodeExecutor

This guard is a pre-execution control. It is designed to run in a Filter or wrapper before a Tool, Skill, MCP Tool, or CodeExecutor starts real execution. It complements sandbox isolation by catching obvious high-risk scripts early and by creating audit records. It also emits fields that can be copied into OpenTelemetry spans.

It cannot replace a sandbox. Static scanning has false positives, false negatives, and bypass risks: obfuscated code, generated strings, encoded payloads, indirect imports, shell expansions, downloaded second-stage scripts, and interpreter-specific behavior can evade static rules. Production deployments should still use least-privilege credentials, network egress controls, filesystem mounts, process and memory limits, timeouts, container or remote sandbox isolation, and post-execution auditing.

## Extending Rules

Add new rules in `trpc_agent_sdk.tools.safety._scanner`. Rules should return `ToolSafetyFinding` with a stable `rule_id`, clear `risk_type`, bounded evidence, and a recommendation. Prefer AST checks for Python and explicit token checks for Bash, then use regex only as a fallback for patterns that are hard to parse.
