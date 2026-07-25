# Tool Script Safety Guard

This example adds a policy-driven safety boundary before script-capable Tools,
MCP Tools, Skills, and CodeExecutors. It scans Python source and Bash commands,
then returns one of:

- `allow`: no configured rule requires intervention.
- `deny`: a high-confidence prohibited pattern was found.
- `needs_human_review`: the scanner cannot prove the operation is safe.

The scanner is deterministic and does not execute the submitted script.

## Run the public samples

From the repository root:

```bash
python examples/tool_safety_guard/tool_safety_check.py \
  --report /tmp/tool_safety_report.json \
  --audit /tmp/tool_safety_audit.jsonl
```

The command scans all 12 files under `samples/`, prints a decision table, and
writes:

- `tool_safety_report.json`: complete findings with rule IDs, redacted evidence,
  recommendations, risk levels, and decisions.
- `tool_safety_audit.jsonl`: compact monitoring events without script content.

Omit `--report` and `--audit` for a read-only scan. The committed JSON and
JSONL files are example snapshots and are not overwritten by default.

Scan one file with:

```bash
python examples/tool_safety_guard/tool_safety_check.py path/to/script.py \
  --language python
```

No sample is executed by this command.

## Policy

`tool_safety_policy.yaml` controls:

- `allowed_domains`: exact hosts or `*.example.org` subdomain patterns.
- `allowed_commands`: Bash executables that do not require human review.
- `denied_paths`: protected absolute prefixes and sensitive basenames.
- `max_timeout_seconds`, `max_output_bytes`, `max_script_bytes`,
  `max_file_write_bytes`, `max_sleep_seconds`, and `max_concurrent_tasks`.
- `rule_decisions` and `rule_risk_levels`: reviewed per-rule overrides.
- `disabled_rules`: emergency suppression for a known false positive.

Changing these fields requires no code change. Invalid or unknown YAML fields
fail policy loading rather than being silently ignored.

## Tool, MCP Tool, and Skill integration

Attach a filter instance to a script-capable Tool:

```python
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools.safety import JsonlAuditSink
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolSafetyScanner
from trpc_agent_sdk.tools.safety import ToolScriptSafetyFilter

policy = ToolSafetyPolicy.from_yaml("tool_safety_policy.yaml")
guard = ToolScriptSafetyFilter(
    scanner=ToolSafetyScanner(policy),
    audit_sink=JsonlAuditSink("tool_safety_audit.jsonl"),
)
tool = FunctionTool(execute_script, filters=[guard])
```

The default extractor recognizes `script`, `code`, or `command`, plus
`language`, `args`/`command_args`, `cwd`/`working_directory`,
`env`/`environment`, and `timeout`/`timeout_seconds`. MCP and Skill tools with
different schemas can pass a custom `request_extractor`. See
`filter_example.py`.

`needs_human_review` is blocked by default. A reviewed application can create a
separate filter with `allow_human_review=True` only after its human approval
workflow has verified the exact report and script hash.

## CodeExecutor integration

Wrap an existing executor:

```python
guarded = SafetyGuardedCodeExecutor(
    executor=container_executor,
    scanner=ToolSafetyScanner(policy),
    audit_sink=JsonlAuditSink("tool_safety_audit.jsonl"),
)
```

Every code block is scanned before delegation. A blocked block prevents the
delegate from running. Allowed output is truncated to `max_output_bytes`. See
`executor_example.py`.

## Rules and decisions

| Category | Representative rules | Default decision |
| --- | --- | --- |
| File access | recursive deletion, protected credentials, file deletion | deny/review |
| Network | non-allowlisted or dynamic targets | deny/review |
| Process | subprocess, shell injection, pipelines, sudo, unknown commands | deny/review |
| Dependencies | pip/npm/apt and similar runtime installs | deny |
| Resources | unbounded loops, fork bomb, long sleep, excessive workers/write | deny/review |
| Secrets | credentials written to logs, files, or network calls | deny |

Reports never contain the complete script or environment map. They include a
SHA-256 digest and bounded evidence snippets. Known environment secret values
and credential-shaped literals are replaced with `[REDACTED]`.

Audit events contain `tool_name`, `decision`, `risk_level`, primary `rule_id`,
all `rule_ids`, `duration_ms`, `redacted`, `blocked`, the script digest, and the
policy version. The JSONL sink intentionally omits script and environment
content.

OpenTelemetry attributes are attached to the active span:

- `tool.safety.decision`
- `tool.safety.risk_level`
- `tool.safety.rule_id`
- `tool.safety.rule_ids`
- `tool.safety.duration_ms`
- `tool.safety.redacted`
- `tool.safety.blocked`
- `tool.safety.policy_version`

## Relationship to sandboxing

This guard complements, but cannot replace, a sandbox. Static scanning can stop
obvious unsafe intent before paying execution cost and provides explainable
policy decisions. A sandbox must still enforce runtime network policy,
filesystem mounts, process identity, syscall restrictions, PID limits,
CPU/memory/disk quotas, timeouts, and output limits.

Known bypasses and limitations include obfuscated or encoded payloads, runtime
downloads, reflection, aliases, generated code, unusual shell expansion,
native binaries, symlink and time-of-check/time-of-use races, DNS rebinding, and
semantic behavior hidden behind apparently safe libraries. AST data-flow
tracking is intentionally shallow. Unknown or dynamic behavior is generally
sent to human review, but false positives and false negatives remain possible.

## Extending rules

Add Python AST checks in `_python_scanner.py`, Bash checks in
`_bash_scanner.py`, and a stable rule ID plus a focused test. Evidence must be
bounded and redacted. Use `deny` only for high-confidence prohibited behavior;
use `needs_human_review` for uncertainty. Keep runtime enforcement in the
sandbox rather than trying to emulate it in static rules.
