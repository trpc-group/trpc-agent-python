# Tool Script Safety Guard

The tool safety guard is an opt-in static pre-execution scanner for Python and Bash-like tool scripts. It is designed to catch common high-risk patterns before local tool execution, return structured reports, write sanitized audit events, and attach optional OpenTelemetry span attributes.

## Threat Model

The guard targets accidental or model-generated tool scripts that read secrets, delete sensitive paths, exfiltrate files, install dependencies, invoke privilege escalation, run dynamic code, or use shell constructs that need review.

Static scanning is not a sandbox. It cannot guarantee runtime safety against obfuscation, encoded payloads, dynamic imports, generated code, environment-dependent behavior, external binaries, or interpreter/runtime bugs. Production systems still need sandboxing, least privilege, network egress control, resource limits, and audit logging.

## Supported Languages

Python scanning uses AST parsing with lightweight alias and constant propagation plus targeted text-pattern fallback.

Bash scanning uses shell tokenization, raw-line operator checks, and cross-command flow checks for sensitive reads piped into network clients.

## Risk Types

Common risk types include `secret_read`, `secret_output`, `secret_exfiltration`, `dangerous_delete`, `network_access`, `process_execution`, `dependency_install`, `privilege_escalation`, `dynamic_code`, `shell_features`, and `resource_exhaustion`.

## Policy Fields

The YAML policy supports:

- `allowed_domains`
- `allowed_commands`
- `denied_paths`
- `max_timeout_seconds`
- `max_output_bytes`
- `long_sleep_seconds`
- `deny_dependency_install`
- `deny_privilege_escalation`
- `review_process_execution`
- `review_unknown_network`
- `review_dynamic_code`
- `review_shell_features`
- `block_on_review`

Wildcard domains such as `*.trusted.internal` match subdomains. Denied paths support user expansion, glob-style filenames, and sensitive basenames such as `.env`, `*.pem`, and `id_rsa`.

## CLI Usage

```bash
python scripts/tool_safety_check.py \
  --file examples/tool_safety/samples/bash_pipe_exfiltration.sh \
  --language bash \
  --policy examples/tool_safety/tool_safety_policy.yaml \
  --output /tmp/tool_safety_report.json \
  --audit-log /tmp/tool_safety_audit.jsonl
```

Exit codes are `0` for allow, `2` for needs human review, `3` for deny, and `1` for CLI errors.

## Filter Usage

```python
from trpc_agent_sdk.tools.safety import ToolSafetyFilter

tool_filter = ToolSafetyFilter(
    policy_path="examples/tool_safety/tool_safety_policy.yaml",
    audit_log_path="/tmp/tool_safety_audit.jsonl",
    block_on_review=True,
)
```

The filter scans request fields such as `script`, `code`, `command`, `cmd`, `python_code`, `bash_code`, and `code_blocks`. A safety block returns `SAFETY_GUARD_BLOCKED` with a `safety_report` and does not set a filter error.

## Wrapper Usage

```python
from trpc_agent_sdk.tools.safety import with_tool_safety

@with_tool_safety(language="bash", block_on_review=True)
def run_command(command: str):
    ...
```

The wrapper supports sync and async callables.

## BashTool Opt-In Usage

```python
from trpc_agent_sdk.tools import BashTool

bash = BashTool(
    enable_safety_guard=True,
    safety_policy_path="examples/tool_safety/tool_safety_policy.yaml",
    safety_audit_log_path="/tmp/tool_safety_audit.jsonl",
    safety_block_on_review=True,
)
```

The default remains disabled to preserve existing behavior.

## UnsafeLocalCodeExecutor Opt-In Usage

```python
from trpc_agent_sdk.code_executors.local import UnsafeLocalCodeExecutor

executor = UnsafeLocalCodeExecutor(
    enable_safety_guard=True,
    safety_policy_path="examples/tool_safety/tool_safety_policy.yaml",
    safety_audit_log_path="/tmp/tool_safety_audit.jsonl",
    safety_block_on_review=True,
)
```

The default remains disabled to preserve existing behavior.

## Report Schema

Reports include `scan_id`, `timestamp`, `decision`, `risk_level`, `findings`, `tool_name`, `language`, `elapsed_ms`, `sanitized`, `blocked`, `summary`, and `telemetry_attributes`.

Each finding includes `rule_id`, `risk_type`, `risk_level`, `decision`, `evidence`, `recommendation`, `message`, `line`, `column`, and `metadata`.

## Audit Schema

Audit JSONL writes one event per scan with `scan_id`, `timestamp`, `tool_name`, `decision`, `risk_level`, `rule_ids`, `elapsed_ms`, `sanitized`, `blocked`, and `trace_attributes`. Evidence and raw scripts are not written to audit events.

## Telemetry Attributes

When OpenTelemetry is installed and a span is recording, the guard sets:

- `tool.safety.scan_id`
- `tool.safety.decision`
- `tool.safety.risk_level`
- `tool.safety.rule_id`
- `tool.safety.blocked`
- `tool.safety.sanitized`
- `tool.safety.tool_name`
- `tool.safety.duration_ms`

## Extension Guide

Add new rule checks in `trpc_agent_sdk.tools.safety._rules`, return `RiskFinding` with sanitized evidence, and cover the behavior with Python/Bash scanner tests. Keep rules deterministic and avoid executing target scripts.

## Validation Matrix

The sample matrix covers safe scripts, dangerous deletion, secret reads, credential files, whitelisted and non-whitelisted network calls, subprocess review, shell injection review, dependency install denial, infinite loop review, sensitive output denial, pipe exfiltration denial, dynamic URL review, and eval review.

## Limitations

Static scanning favors fast deterministic checks over completeness. It can miss obfuscated payloads, encoded commands, generated code, external binary behavior, and runtime-dependent flows. Treat it as a guardrail, not isolation.
