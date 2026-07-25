# Tool Script Safety

`trpc_agent_sdk.tools.safety` provides pre-execution static scanning for Python
and Bash, policy decisions, Tool Filter and CodeExecutor integrations, audit
events, and OpenTelemetry attributes.

## Architecture

The guard has four independent parts:

1. `ToolSafetyScanner` converts `SafetyScanRequest` into a structured
   `SafetyReport`.
2. `ToolScriptSafetyFilter` blocks unsafe Tool, MCP Tool, or Skill arguments
   before their implementation runs.
3. `SafetyGuardedCodeExecutor` scans code blocks before delegating to an
   existing local, container, or remote executor.
4. audit sinks and span attributes expose decisions to monitoring systems.

The default decision precedence is `deny` over `needs_human_review` over
`allow`. Human-review findings fail closed unless an application explicitly
continues after reviewing the exact script hash and report.

## Configuration

Load a strict YAML policy:

```python
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolSafetyScanner

policy = ToolSafetyPolicy.from_yaml("tool_safety_policy.yaml")
scanner = ToolSafetyScanner(policy)
```

The policy configures allowed domains and commands, denied paths, timeout and
output limits, resource heuristics, disabled rules, and per-rule decision or
risk overrides. See
[`examples/tool_safety_guard/tool_safety_policy.yaml`](https://github.com/trpc-group/trpc-agent-python/tree/main/examples/tool_safety_guard/tool_safety_policy.yaml).

## Integration

Attach `ToolScriptSafetyFilter` through a Tool's `filters` list. Its default
extractor recognizes common execution fields such as `script`, `code`,
`command`, `cwd`, `env`, and `timeout`. Supply a custom extractor for another
schema.

Wrap a CodeExecutor with `SafetyGuardedCodeExecutor`. The wrapper preserves the
delegate's executor configuration, rejects disallowed code before delegation,
emits an event for every scanned block, and limits returned output.

The full runnable examples, 12 public scan samples, generated report, and audit
log are under
[`examples/tool_safety_guard`](https://github.com/trpc-group/trpc-agent-python/tree/main/examples/tool_safety_guard).

## Acceptance verification

Run the public scanner and focused suite from the repository root:

```bash
python examples/tool_safety_guard/tool_safety_check.py \
  --report /tmp/tool_safety_report.json \
  --audit /tmp/tool_safety_audit.jsonl
pytest -q tests/tools/safety
```

The public samples produce 2 `allow`, 7 `deny`, and 3
`needs_human_review` decisions. The focused suite contains 47 tests, including
the 500-line scan performance assertion.

## Security boundary

Static analysis is not a sandbox. The guard can be bypassed by obfuscation,
generated code, runtime downloads, native binaries, complex shell expansion,
symlink races, DNS rebinding, or behavior hidden in dependencies. It can also
produce false positives.

Production deployments must still isolate network access, mounts, credentials,
process identity, syscalls, CPU, memory, disk, PIDs, time, and output. The guard
reduces obvious unsafe execution and improves explainability and auditability;
the sandbox contains behavior that is only visible at runtime.
