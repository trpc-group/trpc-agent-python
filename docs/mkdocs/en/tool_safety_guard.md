# Tool Script Safety Guard

[中文版本](tool_safety_guard.zh_CN.md)

Pre-execution static safety scanner for Python and Bash scripts invoked by
Tools, Skills, MCP tools, and CodeExecutors. Produces a structured
``allow`` / ``deny`` / ``needs_human_review`` decision, a redacted report,
JSONL audit events, and OpenTelemetry attributes. **Does not replace
sandbox isolation.**

## What this is

The guard is a **policy-driven static gate** that runs *before* code is
executed. It scans scripts, command-line arguments, working directory,
environment variables, and tool metadata, then applies a catalog of rules
to produce a three-state decision. The decision and the supporting
evidence are emitted as a redacted :class:`SafetyReport`, an audit event,
and OpenTelemetry span attributes / metrics.

```text
+----------------+      +-------------+      +------------------+
| Tool / Skill / | ---> |   Guard     | ---> | allow: proceed   |
| CodeExecutor   |      | (sync scan) |      | deny: block      |
| input          |      |             |      | review: pause    |
+----------------+      +------+------+      +------------------+
                               |
                               v
                  +-----------+-----------+
                  | Audit | Telemetry | OTel |
                  +-----------------------+
```

The static guard is **one layer** of defense. It complements (never
replaces) container/sandbox isolation, network egress policy, OS
permissions, and runtime resource limits.

## What this is not

* **Not a sandbox.** Static analysis cannot see what the code will do at
  runtime once it has been allowed. Production deployments must still
  use unprivileged containers with read-only mounts, egress allowlists,
  cgroup/ulimit bounds, and timeouts.
* **Not complete.** Obfuscation, runtime concatenation, reflection,
  native extensions, downloaded payloads, symlink races, and behavior
  that depends on runtime state can all bypass a static scanner. The
  guard converts uncertainty into ``needs_human_review`` rather than
  silently allow.
* **Not a secret.** The policy file is the source of truth; treat it as
  sensitive. Anyone who can change ``rule_overrides`` or
  ``allowed_commands`` can weaken the guard.

## Responsibility matrix

| Layer | Owns |
|---|---|
| **SafetyWrappedCallable / SafetyCheckedExecutor** | Current enforcement path: scan, await audit, then delegate only when allowed |
| **ToolScriptSafetyFilter** | Normalizes and records decisions for wrappers and a future SDK terminal hook |
| **Wrapper / Sandbox / Runtime** | Runtime isolation: CPU, memory, PID, FS, network hard limits |
| **Audit / Telemetry** | Decision evidence; required audit persistence is part of the wrapper's fail-closed gate |

## Quick start

```bash
python scripts/tool_safety_check.py \
    --policy trpc_agent_sdk/tools/safety/examples/tool_safety_policy.yaml \
    --language python \
    --script-file trpc_agent_sdk/tools/safety/examples/samples/03_dangerous_delete.py \
    --output tool_safety_report.json \
    --audit-file tool_safety_audit.jsonl
echo $?  # 0=allow, 2=deny, 3=review, 4=input/policy error
```

Run the manifest to scan all 14 public samples:

```bash
python scripts/tool_safety_check.py \
    --policy trpc_agent_sdk/tools/safety/examples/tool_safety_policy.yaml \
    --manifest trpc_agent_sdk/tools/safety/examples/samples/manifest.yaml \
    --manifest-output trpc_agent_sdk/tools/safety/examples/manifest_run.json \
    --audit-file trpc_agent_sdk/tools/safety/examples/tool_safety_audit.jsonl
```

## Programmatic usage

```python
from trpc_agent_sdk.tools.safety import (
    ToolSafetyGuard,
    load_safety_policy,
    SafetyScanRequest,
    ScriptLanguage,
)

policy = load_safety_policy("trpc_agent_sdk/tools/safety/examples/tool_safety_policy.yaml")
guard = ToolSafetyGuard(policy)

request = SafetyScanRequest(
    tool_name="workspace_exec",
    language=ScriptLanguage.BASH,
    script="rm -rf /tmp/x",
    cwd="/tmp",
    env={"PATH": "/usr/bin"},
)
report = guard.scan(request)
print(report.decision, report.rule_ids)
```

### Wrapping a callable

```python
import subprocess
from trpc_agent_sdk.tools.safety import SafetyWrappedCallable
from trpc_agent_sdk.tools.safety import ToolSafetyGuard, load_safety_policy, ScriptLanguage

guard = ToolSafetyGuard(load_safety_policy("policy.yaml"))
safe_run = SafetyWrappedCallable(
    guard, subprocess.run,
    tool_name="subprocess.run",
    language=ScriptLanguage.BASH,
    script_pos=0,
)
safe_run("ls -la")  # raises BlockedExecutionError if policy denies
```

Call ``await safe_run.call_async(...)`` instead when the delegate or caller
already runs inside an event loop; this preserves the required-audit
before-delegate guarantee.
For a tool-style callable, set ``argv_kw``, ``cwd_kw``, ``env_kw``,
``metadata_kw``, and ``output_bytes_kw`` to the corresponding argument
names so the normalized request contains every available execution field.

### Wrapping a code executor

```python
from trpc_agent_sdk.tools.safety import SafetyCheckedExecutor
from trpc_agent_sdk.tools.safety import ToolSafetyGuard, load_safety_policy, ScriptLanguage

guard = ToolSafetyGuard(load_safety_policy("policy.yaml"))
safe_executor = SafetyCheckedExecutor(
    guard,
    delegate=real_executor,
    language=ScriptLanguage.PYTHON,
    effective_timeout_seconds=30,
)
await safe_executor.execute_code(code_input)
```

## Rule catalog

The guard applies one stable rule per risk category. Rule IDs never
change between releases so policy overrides remain stable.

| Rule ID | Category | Default decision | What it catches |
|---|---|---|---|
| `FILE001_RECURSIVE_DELETE` | file | deny | `shutil.rmtree`, `rm -rf` |
| `FILE002_DENIED_WRITE` | file | deny | Writes to denied paths |
| `FILE003_CREDENTIAL_READ` | file | deny | Reads of `.ssh`, `id_rsa`, `.pem`, credentials |
| `FILE004_DOTENV_READ` | file | deny | Reads of `.env` files |
| `NET001_DOMAIN_NOT_ALLOWED` | network | deny | Requests/curl/wget to non-allowlisted hosts |
| `NET002_DYNAMIC_TARGET` | network | review | Computed network destination |
| `NET003_IP_LITERAL` | network | deny | IP literal when `deny_ip_literals` is enabled |
| `PROC001_PROCESS_EXEC` | process | review | Subprocess or command not on allow list |
| `PROC002_SHELL_INJECTION` | process | deny | `shell=True` with shell grammar |
| `PROC003_SHELL_OPERATOR` | process | review | `;`, `&&`, `\|`, `&`, command substitution |
| `PROC004_PRIVILEGE` | process | deny | `sudo`, `su`, `doas` |
| `DEP001_ENV_MUTATION` | dependency | deny | `pip install`, `npm install`, `apt install` |
| `RES001_UNBOUNDED_LOOP` | resource | deny | `while True` without break |
| `RES002_FORK_BOMB` | resource | deny | Classic `:(){ :\|:& };:` pattern |
| `RES003_LONG_SLEEP` | resource | deny | Sleeps exceeding policy limit |
| `RES004_CONCURRENCY` | resource | deny | Fan-out exceeding `max_parallel_tasks` or `max_processes` |
| `RES005_LARGE_WRITE` | resource | deny | Writes exceeding `max_file_write_bytes` |
| `SECRET001_LOG_SINK` | secret | deny | Tainted value into print/log |
| `SECRET002_FILE_SINK` | secret | deny | Tainted value into file write |
| `SECRET003_NETWORK_SINK` | secret | deny | Tainted value into network payload |
| `PARSE001_UNCERTAIN` | analysis | review | Syntax error or unknown construct |
| `OBF001_DYNAMIC_EXEC` | analysis | review | `eval`, `exec`, indirect Bash execution, interpreter payloads |
| `SAFE000` | safe | allow | No findings |
| `GUARD001_INTERNAL_ERROR` | analysis | deny | Internal guard failure (fail closed) |

## Policy reference

```yaml
version: "1"

defaults:
  unknown_construct: needs_human_review   # allow | needs_human_review | deny
  guard_error: deny                       # fail-closed default
  human_review_blocks_execution: true     # review blocks the wrapper

limits:
  max_timeout_seconds: 60
  max_output_bytes: 1048576
  max_script_bytes: 262144
  max_sleep_seconds: 30
  max_parallel_tasks: 16
  max_processes: 8
  max_file_write_bytes: 10485760

network:
  allow_domains:
    - api.github.com
    - "*.internal.example.com"   # one sub-domain level only
  deny_ip_literals: true

commands:
  allow: [python, python3, pytest, git]
  deny: [sudo, su, doas, chmod, chown, mount]

paths:
  deny:
    - "~/.ssh"
    - "/etc"
    - "/root"
    - ".env"
    - "**/*credentials*"

dependencies:
  decision: deny

sensitive_env_key_patterns:
  - "*KEY*"
  - "*TOKEN*"
  - "*PASSWORD*"
  - "*SECRET*"
  - "*CREDENTIAL*"

tools:
  workspace_exec:
    execution_capable: true
    language: bash
    script: command
    cwd: cwd
    env: env
    timeout: timeout_sec

rule_overrides: {}
# Override any rule's decision. Example:
#   DEP001_ENV_MUTATION: needs_human_review

audit:
  enabled: true
  required: true                  # fail-closed when audit write fails
  path: tool_safety_audit.jsonl
```

## Hot-reload behavior

The guard does not watch the YAML file. Construct a new
``ToolSafetyGuard`` (and re-register the filter / wrapper) after editing
the policy. The ``policy_hash`` in every report / audit event lets
operators correlate which policy produced which decision.

## Telemetry

When OpenTelemetry is active the guard sets these low-cardinality span
attributes on the current span:

```text
trpc_agent_sdk.tools.safety.decision
trpc_agent_sdk.tools.safety.risk_level
trpc_agent_sdk.tools.safety.rule_id           # comma-separated, bounded to 8 entries
trpc_agent_sdk.tools.safety.blocked
trpc_agent_sdk.tools.safety.redacted
trpc_agent_sdk.tools.safety.scan_duration_ms
trpc_agent_sdk.tools.safety.policy_hash
```

Metrics emitted (no-op when OTel is absent):

```text
trpc_agent.tool_safety.scan_count{decision,risk_level,tool_name}
trpc_agent.tool_safety.block_count{decision,rule_id,tool_name}
trpc_agent.tool_safety.scan_duration_ms{decision,tool_name}
```

Evidence snippets, env values, script hashes, and command strings are
never emitted as span attributes or metric labels.

## CLI exit codes

| Code | Meaning |
|---|---|
| 0 | Final decision was ``allow`` |
| 2 | Final decision was ``deny`` |
| 3 | Final decision was ``needs_human_review`` |
| 4 | Invalid input, policy, or required-audit error |

## Integration with the SDK

The current SDK does not expose a terminal filter phase after
``ToolCallbackFilter``. A configured ``filters=`` instance can therefore
scan arguments that a later callback changes; it is not a secure enforcement
point. Use ``SafetyWrappedCallable`` or ``SafetyCheckedExecutor`` today:
both scan, await the audit event, and only then invoke their delegate.

``ToolScriptSafetyFilter`` provides matching ``_before``/``_after`` hooks
and a ``terminal_before_handler`` marker for a future SDK terminal phase.
That marker is metadata only until the framework implements ordering after
all argument-mutating callbacks. The wrapper remains mandatory until then.

## Custom rules

Implement :class:`SafetyRule` and pass the rule list explicitly:

```python
from trpc_agent_sdk.tools.safety import ToolSafetyGuard, SafetyScanRequest

class MyRule:
    rule_id = "CUSTOM001_MY_RULE"

    def scan(self, request, policy):
        # Return an iterable of SafetyFinding.
        return []

guard = ToolSafetyGuard(
    policy,
    rules=[*default_rules(), MyRule()],
)
```

Rules must be pure: no file I/O, network access, or process creation.

## Known limitations and bypasses

* **Obfuscation.** Base64-decoded payloads, hex decoding, and similar
  transforms hide intent. The guard emits ``OBF001_DYNAMIC_EXEC`` when
  it sees ``eval``/``exec``/``compile``/dynamic imports, but cannot
  reconstruct the decoded content statically.
* **Indirect data flow.** Taint propagation is deliberately shallow:
  literals, names, direct assignments, f-strings, concatenation, and
  shallow container construction. Deeper flows surface as review.
* **Symlink races.** Static path matching cannot resolve symlinks. The
  sandbox must enforce filesystem boundaries.
* **Native extensions.** ``ctypes.CDLL(...)``, ``cffi.dlopen(...)``,
  and similar primitives are not statically inspectable.
* **Runtime downloads.** A script that downloads and executes a payload
  in two stages defeats static analysis. Network egress policy and
  sandboxing are mandatory.
* **Runtime limits.** The wrapper validates the declared timeout and caps
  returned output, but it cannot impose CPU, memory, PID, file-size, or
  network limits on an arbitrary executor. Configure those in the sandbox
  or CodeExecutor runtime as well.
* **Shell grammar holes.** The bash lexer-lite is conservative: any
  unbalanced quote or unsupported substitution becomes
  ``PARSE001_UNCERTAIN``.

When in doubt, the guard converts uncertainty into
``needs_human_review``. Operators must approve explicitly before
execution resumes.

## Test plan

```bash
python -m pytest tests/tool_safety/ -v
```

Coverage:

* ``test_models``: immutability, ``repr=False`` on script/env, label
  serialization.
* ``test_policy``: YAML validation, normalization, hash stability,
  wildcard semantics.
* ``test_redaction``: env scrubbing, secret patterns, evidence bounding.
* ``test_python_scanner``: per-rule Python AST detection.
* ``test_bash_scanner``: per-rule Bash lexer-lite detection.
* ``test_cross_field_scanner``: cwd / argv / env / timeout correlation.
* ``test_guard``: aggregation, de-duplication, fail-closed on errors.
* ``test_audit``: JSONL sink, redaction invariants.
* ``test_tool_adapter``: built-in adapters and policy overrides.
* ``test_filter``: ``check`` / ``enforce`` semantics, audit per call.
* ``test_wrapper``: callable + executor wrapping, deny does not delegate.
* ``test_cli``: exit codes, manifest output.
* ``test_performance``: 500-line Python and Bash scripts in <1s p95.
* ``test_integration``: 14 manifest samples match expected decisions.

## File layout

```text
tool/
  __init__.py                   # public re-exports
  safety/
    __init__.py
    _exceptions.py
    _models.py
    _policy.py
    _redaction.py
    _facts.py
    _rules.py                   # SafetyRule protocol + rule catalog
    _python_scanner.py
    _bash_scanner.py
    _cross_field_scanner.py
    _guard.py                   # ToolSafetyGuard
    _audit.py                   # AuditSink, JsonlAuditSink, InMemoryAuditSink
    _telemetry.py               # OTel span attrs + metrics (no-op safe)
    _tool_adapter.py            # ToolInputAdapter + built-ins
    _filter.py                  # ToolScriptSafetyFilter (terminal)
  wrapper.py                    # SafetyWrappedCallable, SafetyCheckedExecutor
scripts/
  tool_safety_check.py          # CLI
tests/tool_safety/              # safety guard tests
trpc_agent_sdk/tools/safety/examples/
  tool_safety_policy.yaml       # sample policy
  samples/                      # 14 public samples + manifest
  tool_safety_report.json       # generated report
  tool_safety_audit.jsonl       # generated audit log
  manifest_run.json             # manifest execution summary
docs/
  tool_safety_guard.md          # this document
  tool_safety_guard.zh_CN.md    # Chinese version
```
