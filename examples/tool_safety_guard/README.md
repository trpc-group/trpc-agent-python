# Tool Safety Guard Example

This example shows how to scan tool scripts before execution with
`trpc_agent_sdk.tools.safety`. The scripts in `samples.yaml` are text fixtures
only. They are scanned, never executed.

## Files

- `tool_safety_policy.yaml`: example policy with allowlisted domains, allowed
  commands, denied paths, and resource limits.
- `samples.yaml`: 12 public samples covering safe and risky Python/Bash inputs.
- `run_safety_scan.py`: regenerates `tool_safety_report.json` and
  `tool_safety_audit.jsonl`.
- `tool_safety_report.json`: aggregate structured report for all samples.
- `tool_safety_audit.jsonl`: audit-safe JSONL summary events.

## Standalone Scanning

Run the example from the repository root:

```bash
python examples/tool_safety_guard/run_safety_scan.py
```

Run the generic CLI:

```bash
python scripts/tool_safety_check.py \
  --samples examples/tool_safety_guard/samples.yaml \
  --policy examples/tool_safety_guard/tool_safety_policy.yaml \
  --report-out /tmp/tool_safety_report.json \
  --audit-out /tmp/tool_safety_audit.jsonl
```

Sample scans verify `expected_decision` and `expected_rules` by default. Add
`--no-verify` when you only want to produce reports from samples.

Scan one file:

```bash
python scripts/tool_safety_check.py \
  --file ./script.py \
  --language python \
  --policy examples/tool_safety_guard/tool_safety_policy.yaml
```

## Tool Filter Integration

`ToolSafetyFilter` is explicit opt-in. It scans script-like tool arguments
before the tool handler runs:

```python
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools.safety import ToolSafetyFilter

tool = FunctionTool(
    run_shell_command,
    filters=[ToolSafetyFilter(policy_path="examples/tool_safety_guard/tool_safety_policy.yaml")],
)
```

The registered name `tool_safety_guard` uses the default policy:

```python
tool = FunctionTool(run_shell_command, filters_name=["tool_safety_guard"])
```

Use `filters=[ToolSafetyFilter(...)]` when a custom policy, scanner, or audit
logger is required.

## CodeExecutor Wrapper Integration

`SafetyGuardedCodeExecutor` wraps any `BaseCodeExecutor` and scans
`CodeExecutionInput` before delegating execution:

```python
from trpc_agent_sdk.code_executors import UnsafeLocalCodeExecutor
from trpc_agent_sdk.tools.safety import SafetyGuardedCodeExecutor

executor = SafetyGuardedCodeExecutor(
    delegate=UnsafeLocalCodeExecutor(),
    policy_path="examples/tool_safety_guard/tool_safety_policy.yaml",
)
```

The wrapper mirrors delegate fields such as `workspace_runtime`,
`code_block_delimiters`, and retry settings when it is constructed. Later
direct changes to the delegate are not automatically synchronized back to the
wrapper.

## Policy Fields

The policy controls scanner behavior without code changes:

- `allowed_domains`: domains permitted for network egress.
- `allowed_commands`: command names allowed by shell scans.
- `denied_paths`: file paths or patterns that must not be accessed.
- `sensitive_env_keys`: environment key patterns treated as secrets.
- `review_blocks_execution`: whether `needs_human_review` blocks execution.
- `fail_closed`: whether scanner errors block execution.
- `max_timeout_seconds`, `max_output_bytes`, `max_script_lines`,
  `max_sleep_seconds`: resource and behavior limits.
- `rules`: optional per-rule overrides for `enabled`, `decision`, or
  `risk_level`.

## Rule Extension

Built-in rules live in the safety rule catalog and produce findings with:

- `rule_id`
- `risk_type`
- `risk_level`
- `decision`
- `message`
- `evidence`
- `recommendation`

To add a rule, extend the catalog, add scanner detection logic, and cover the
new rule with policy/scanner tests. Evidence must be short and redacted.

## Reports, Audit, and OpenTelemetry

`tool_safety_report.json` includes one `SafetyReport` per sample. Each report
contains the final decision, risk level, findings, evidence snippets, and
recommendations.

`tool_safety_audit.jsonl` contains one audit-safe event per sample. It records
tool name, decision, risk level, rule ids, elapsed time, redaction state,
blocked state, language, policy name, scanner version, and finding count. It
does not include full script content or secret values.

When OpenTelemetry is enabled, the safety helper writes attributes such as:

- `tool.safety.decision`
- `tool.safety.risk_level`
- `tool.safety.rule_ids`
- `tool.safety.blocked`
- `tool.safety.redacted`
- `tool.safety.elapsed_ms`
- `tool.safety.finding_count`
- `tool.safety.policy_name`
- `tool.safety.language`

## Known Limits

This guard is a static pre-execution scanner. It can miss obfuscated code,
runtime-generated commands, encoded payloads, tool behavior hidden behind
libraries, and policy bypasses that only appear at execution time. It can also
produce false positives for benign maintenance scripts that look risky.

The guard does not replace sandbox isolation. Production deployments should
still use least-privilege credentials, isolated workspaces, resource limits,
network controls, runtime monitoring, and audit retention. Static scanning is
one layer: it provides early blocking, human review signals, and observability
before code reaches the executor.
