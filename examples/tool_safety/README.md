# Tool Script Safety Guard

The tool safety guard is an opt-in static pre-execution scanner for Python and
Bash-like tool scripts. It catches common high-risk patterns before local tool
execution, returns structured reports, writes sanitized audit events, and can
attach OpenTelemetry span attributes.

## Threat Model

The guard targets accidental or model-generated tool scripts that read secrets,
delete sensitive paths, exfiltrate files, install dependencies, invoke privilege
escalation, run dynamic code, or use shell constructs that need review.

Static scanning is not a sandbox. It cannot guarantee runtime safety against
obfuscation, encoded payloads, dynamic imports, generated code,
environment-dependent behavior, external binaries, or interpreter/runtime bugs.
Production systems still need sandboxing, least privilege, network egress
control, resource limits, and audit logging.

## Supported Languages

Python scanning uses AST parsing with lightweight alias and constant propagation
plus targeted text-pattern fallback.

Bash scanning uses shell tokenization, raw-line operator checks, and
cross-command flow checks for sensitive reads piped into network clients.

Argv-style inputs are scanned with the script or command. Interpreter forms such
as `python -c ...`, `bash -c ...`, and `bash -lc ...` are scanned using the
language of the inline code.

## Risk Types

Common risk types include `secret_read`, `secret_output`, `secret_exfiltration`,
`dangerous_delete`, `network_access`, `process_execution`,
`dependency_install`, `privilege_escalation`, `dynamic_code`, `shell_features`,
and `resource_exhaustion`.

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

Wildcard domains such as `*.trusted.internal` match subdomains. Denied paths
support user expansion, glob-style filenames, and sensitive basenames such as
`.env`, `*.pem`, and `id_rsa`.

## Policy Files

`tool_safety_policy.yaml` is the canonical example policy used by the manifest
report. `policy.yaml` is kept as a compatibility alias for shorter CLI examples
and contains the same settings.

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

The CLI also accepts a positional file path:

```bash
python scripts/tool_safety_check.py \
  examples/tool_safety/samples/safe_bash.sh \
  --language bash \
  --policy examples/tool_safety/tool_safety_policy.yaml
```

Use strict policy mode when validating reviewed policy files:

```bash
python scripts/tool_safety_check.py \
  examples/tool_safety/samples/safe_bash.sh \
  --language bash \
  --policy examples/tool_safety/tool_safety_policy.yaml \
  --strict-policy
```

## Filter Usage

```python
from trpc_agent_sdk.tools.safety import ToolSafetyFilter

tool_filter = ToolSafetyFilter(
    policy_path="examples/tool_safety/tool_safety_policy.yaml",
    audit_log_path="/tmp/tool_safety_audit.jsonl",
    block_on_review=True,
)
```

The filter scans request fields such as `script`, `code`, `command`, `cmd`,
`python_code`, `bash_code`, and `code_blocks`.

It also scans argv-style fields:

- `command_args`
- `args`
- `argv`
- nested dict-like tool inputs containing those fields

A safety block returns `SAFETY_GUARD_BLOCKED` with a `safety_report` and does
not set a filter error.

## Wrapper Usage

```python
from trpc_agent_sdk.tools.safety import with_tool_safety

@with_tool_safety(language="bash", block_on_review=True)
def run_command(command: str):
    ...
```

The wrapper supports sync and async callables.

Tool and Skill-like payloads can opt in through the same Filter/Wrapper path.
MCP-like payloads can be protected through the generic Filter/Wrapper examples.
See `skill_wrapper_example.py` for an async Skill-like handler that scans
`python_code`, argv-style `command_args`, nested dict-like payloads, and
MCP-like `params.arguments` input before calling the wrapped function.

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

Reports include `scan_id`, `timestamp`, `decision`, `risk_level`, `findings`,
`tool_name`, `language`, `elapsed_ms`, `sanitized`, `blocked`, `summary`, and
`telemetry_attributes`.

Each finding includes `rule_id`, `risk_type`, `risk_level`, `decision`,
`evidence`, `recommendation`, `message`, `line`, `column`, and `metadata`.

## Sample Manifest

`samples/manifest.yaml` is the source of truth for the sample validation matrix. Each entry contains:

- `file`
- `language`
- `expected_decision`
- `required_rule_id`
- `category`
- `high_risk`

Tests read this manifest directly. Adding a new sample requires one manifest
entry with the expected scanner outcome and at least one rule that must appear
unless the sample is expected to allow.

Run manifest validation with:

```bash
python -m pytest tests/tools/safety/test_manifest_validation.py -q
```

## All Reports

`all_reports.json` is generated by statically scanning every manifest sample
with `tool_safety_policy.yaml`. It stores:

- expected decision
- actual decision
- required-rule match
- category
- high-risk flag
- full sanitized report

The manifest report normalizes dynamic `scan_id`, `timestamp`, and duration
fields so rerunning the generator produces a stable review artifact.

Regenerate it with:

```bash
python scripts/tool_safety_manifest_report.py --strict-policy
```

This command is CI-friendly: it exits with status `1` if any sample decision
differs from the manifest, any required rule is missing, or strict policy
validation fails. Failure output includes the sample file, expected decision,
actual decision, required rule, and actual rule IDs.

The current corpus contains 52 samples with 52/52 decision matches and 52/52
required-rule matches.

## Audit Schema

Audit JSONL writes one event per scan with `scan_id`, `timestamp`, `tool_name`,
`decision`, `risk_level`, `rule_ids`, `elapsed_ms`, `sanitized`, `blocked`, and
`trace_attributes`. Evidence and raw scripts are not written to audit events.

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

Add new rule checks in `trpc_agent_sdk.tools.safety._rules`, return
`RiskFinding` with sanitized evidence, and cover the behavior with Python/Bash
scanner tests. Keep rules deterministic and avoid executing target scripts.

For local, in-process customization, register a small callable rule:

```python
from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import RiskFinding
from trpc_agent_sdk.tools.safety import RiskLevel
from trpc_agent_sdk.tools.safety import register_safety_rule


def block_marker(context):
    if "CUSTOM_MARKER" not in context.script:
        return []
    return [
        RiskFinding(
            rule_id="CUSTOM_MARKER_BLOCK",
            risk_type="custom",
            risk_level=RiskLevel.HIGH,
            decision=Decision.DENY,
            evidence="CUSTOM_MARKER",
            recommendation="Remove the custom marker before execution.",
            message="Custom marker detected.",
        )
    ]


register_safety_rule("marker", block_marker, languages=["python", "bash"])
```

Custom rules are called after built-in rules. If a custom rule raises, the
scanner emits a `needs_human_review` finding instead of allowing execution.
The API intentionally does not load rules through dynamic imports.

## Validation Matrix

The sample matrix covers:

- safe Python and Bash scripts
- dangerous and dynamic deletion
- secret reads, credential files, and sensitive taint propagation
- whitelisted and non-whitelisted network calls
- `requests.Session`, `httpx.Client`, `aiohttp.ClientSession`,
  `urllib.request`, and sockets
- command-line argument scanning for argv and interpreter forms
- bypass regression samples for `Path.home()`, `subprocess` interpreter forms,
  shell `bash -c` / `sh -c`, `find -delete`, `xargs rm -rf`, and curl data-file
  exfiltration
- subprocess review and shell injection review
- dependency install denial and eval review
- infinite loops, long waits, large allocation review, unbounded output review,
  and large zero-fill write review
- sensitive output denial and pipe exfiltration denial
- Bash network egress through `curl`, `wget`, `nc`, `netcat`, `socat`, `ssh`,
  `scp`, `rsync`, `openssl s_client`, and `/dev/tcp`
- dynamic URL review, shell features, and background processes

## Limitations

Static scanning favors fast deterministic checks over completeness. It can miss
obfuscated payloads, encoded commands, generated code, external binary behavior,
and runtime-dependent flows.

Treat it as a pre-execution guardrail, not isolation. It does not replace:

- process sandboxing
- least-privilege filesystem permissions
- network egress controls
- resource limits
- runtime audit and monitoring
