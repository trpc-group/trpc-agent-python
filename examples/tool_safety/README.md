# Tool Script Safety Guard

This example statically scans Python and Bash payloads before a Tool, Skill, MCP
Tool, or CodeExecutor invokes them. It does not execute the sample files.

Run all public samples:

```bash
python scripts/tool_safety_check.py \
  --policy examples/tool_safety/tool_safety_policy.yaml \
  --output examples/tool_safety/tool_safety_report.json \
  --audit examples/tool_safety/tool_safety_audit.jsonl \
  examples/tool_safety/samples/*.py \
  examples/tool_safety/samples/*.sh
```

The command exits with `0` when every file is allowed, `2` when review is the
worst result, `3` when any file is denied, and `4` on scanner, policy, or input
failure. An exit code other than zero must prevent unattended execution.

## Decision contract

- `allow`: the configured static rules found no risk; the caller may execute
  within its sandbox and resource limits.
- `deny`: the caller must not invoke the real executor.
- `needs_human_review`: the caller must stop and return a pending-review
  result. It is not a warning mode. With no trusted approver, execution remains
  blocked.

Approval cannot be supplied by model-controlled Tool arguments, MCP arguments,
script content, or ordinary metadata. This version intentionally provides no
approval UI or request-level bypass.

## Integration

Use `ToolScriptSafetyFilter` in a Tool filter list, or wrap an existing
`BaseCodeExecutor` with `SafetyGuardedCodeExecutor`. Both routes share
`ToolSafetyGuard`, produce one audit event per invocation, block before the
delegate, and fail closed when scanning or required auditing fails.
The CodeExecutor wrapper also requires the delegate to expose a positive
execution timeout (or a Cube `execute_timeout`) and scans any declared
execution environment before delegation.

`tool.safety.decision`, `tool.safety.risk_level`, `tool.safety.rule_id`,
duration, redaction, blocking, and policy version are attached to an active
OpenTelemetry span on a best-effort basis.

Policy YAML is strict: duplicate keys, unknown fields, URLs in the hostname
allowlist, and invalid limits fail during loading. Evidence is redacted and
bounded before entering reports, JSONL, logs, or tracing.

## Boundary and extension

The scanner uses Python AST plus conservative Bash structure checks. It can
miss obfuscated, dynamically generated, encoded, native, or runtime-dependent
behavior, and conservative rules can require review for safe programs. Add a
rule in `_scanner.py`, give it a stable rule id, and add both positive and
negative tests before changing policy defaults.

This guard complements but cannot replace a sandbox. A production executor
still needs filesystem and network isolation, least-privilege credentials,
native timeout/output limits, and process-tree cleanup. The local command
runner starts a separate process group on POSIX and escalates from terminate to
kill on timeout or cancellation, terminating descendants and reaping the
process it owns. Other platforms still require executor-specific process-tree
controls.
