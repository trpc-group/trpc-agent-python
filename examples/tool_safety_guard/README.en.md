# Tool Script Safety Guard

This example provides deterministic, pre-execution checks for Python scripts and
POSIX/Bash commands. It can run as a standalone scanner, a Tool Filter, or a
wrapper around a CodeExecutor or workspace program runner.

[中文说明](./README.md)

## Run the public samples

From the repository root:

```bash
python examples/tool_safety_guard/run_safety_check.py \
  --manifest examples/tool_safety_guard/samples/manifest.yaml \
  --policy examples/tool_safety_guard/tool_safety_policy.yaml \
  --cwd /tmp/tool-safety-workspace \
  --check-expected \
  --report /tmp/tool_safety_report.json \
  --audit /tmp/tool_safety_audit.jsonl
```

The CLI only reads and scans scripts; it never executes them. Exit codes for a
single input are `0` for `allow`, `2` for `needs_human_review`, and `3` for
`deny`.

The 28-sample manifest includes Bash continuations, comment and single-quote
safe cases, constant path propagation, session keyword URLs, bare curl targets,
and `while 1`. More detailed argv/env, dynamic shell, sensitive upload, and
report-redaction cases live in `tests/tools/safety/`.

## Standalone scanner

```python
from trpc_agent_sdk.tools.safety import SafetyScanRequest
from trpc_agent_sdk.tools.safety import SafetyScanner

scanner = SafetyScanner.from_yaml(
    "examples/tool_safety_guard/tool_safety_policy.yaml"
)
report = scanner.scan(
    SafetyScanRequest(
        content='import shutil\nshutil.rmtree("/")',
        language="python",
        cwd="/tmp/tool-safety-workspace",
        tool_name="example",
    )
)
print(report.model_dump_json(indent=2))
```

Every report contains non-null `decision`, `risk_level`, `rule_id`, `evidence`,
and `recommendation`. An allow result with no findings uses the stable
`ALLOW-000` summary.

## Tool Filter

`BaseTool.run_async()` runs attached filters before the handler:

```python
from trpc_agent_sdk.tools import BashTool
from trpc_agent_sdk.tools.safety import BashToolBlockResponseAdapter
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.tools.safety import ToolSafetyFilter

scanner = SafetyScanner.from_yaml(
    "examples/tool_safety_guard/tool_safety_policy.yaml"
)
tool = BashTool(cwd="/tmp/tool-safety-workspace")
tool.add_one_filter(
    ToolSafetyFilter(
        scanner,
        language="bash",
        content_field="command",
        cwd_field="cwd",
        timeout_field="timeout",
        default_timeout_seconds=30,
        block_response_adapter=BashToolBlockResponseAdapter(),
    )
)
```

Both `deny` and `needs_human_review` stop the handler and return a structured,
redacted report. The same `filters=[...]` constructor argument can attach the
filter to an `MCPTool`; MCP tool-selection predicates are not execution filters.

`StreamingProgressTool` is an explicit exception: the framework calls its
`run_streaming()` method directly instead of `BaseTool.run_async()`. Such tools
need a dedicated wrapper and must not rely on a regular Tool Filter.

## Skill and CodeExecutor wrappers

```python
from trpc_agent_sdk.tools.safety import GuardedProgramRunner
from trpc_agent_sdk.tools.safety import SafetyGuard

guard = SafetyGuard(scanner)
runner = GuardedProgramRunner(
    workspace_runtime.runner(ctx),
    guard,
    tool_name="SkillRun",
)
result = await runner.run_program(workspace, program_spec, ctx)
```

Blocked programs return the existing `WorkspaceRunResult` type with exit code
126. Code executors use the same guard and preserve `CodeExecutionResult`:

For a guarded program call, provider environment values are resolved exactly
once before scanning. An empty result or handled provider failure is also final
for that call: retrying only inside the delegate could execute new provider
values that were never scanned. Start a new invocation if the provider should
be retried.

```python
from trpc_agent_sdk.tools.safety import GuardedCodeExecutor

safe_executor = GuardedCodeExecutor(real_executor, guard)
```

`GuardedCodeExecutor` aggregates all code blocks into one decision and one
audit event. Unknown languages, truncated executable input files, and invalid
input-file paths cannot be allowed automatically. Files whose suffix or MIME
type identifies Python or Bash are scanned before delegation; ordinary data
files such as CSV or images are not treated as scripts.

## Policy and custom rules

The YAML policy changes domain and command allowlists, denied paths, resource
limits, and built-in rule enablement/actions without code changes. Unknown keys
and built-in rule IDs are rejected. Each policy file declares
`api_version: trpc-agent.io/tool-safety/v1`, `kind: ToolSafetyPolicy`, a content
`version`, and a `policy_id`.

An allowed entry without `/` matches a command name resolved through `PATH`.
An absolute executable path such as `/usr/bin/tool` must be listed exactly;
relative path allowlist entries are rejected, and sharing a basename with an
allowed command does not make `/tmp/tool` trusted. Environment overrides that
can change command or module resolution, including `PATH`, `BASH_ENV`,
`LD_PRELOAD`, and `PYTHONPATH`, stop automatic execution. Dynamic Python
`subprocess` `env` and `executable` overrides require the same scrutiny.

`NET-002`, `PROC-003`, `PROC-UNKNOWN-001`, `PARSE-001`, and
`POLICY-INPUT-001` represent dynamic targets, unknown semantics, incomplete
analysis, or invalid input. YAML cannot disable these completeness rules or
relax them to `allow`, although it may tighten them to `deny`. Rules for
fully identified operations remain configurable.

`policy_relaxed` is set only when a relaxed policy action applies to a rule
actually present in the current report. Unrelated global relaxations do not
mark every audit event as relaxed.

Project-specific matching logic can be supplied without editing the scanner:

```python
class ProjectRule:
    rule_id = "PROJECT-001"

    def analyze(self, request, policy):
        # Return zero or more SafetyFinding objects. Never execute request.content.
        ...


scanner = SafetyScanner(custom_rules=[ProjectRule()])
```

Custom rules must return `SafetyFinding` objects with the same `rule_id`.
Evidence is redacted and bounded before it enters the final report. YAML
`rule_overrides` applies to built-in rule IDs; a custom rule owns its action.

The scanner propagates simple callable/client aliases within their lexical
scope, such as `run = os.system`, `fetch = requests.get`, and
`fetch = session.get`. Conditional or cross-scope rebinding that has no unique
static value requires human review. It also performs bounded argument and
return-value propagation through local wrapper functions. Bash is parsed with a tree-sitter syntax
tree before command arguments are analyzed. Common wrappers including `env`,
`command`, `exec`, `nice`, `timeout`, `nohup`, `setsid`, and `xargs` are
unwrapped before their static command arguments are checked. Container
lookups, closures, and runtime reflection remain static-analysis limitations.

## Audit, telemetry, and failure behavior

Audit events contain bounded metadata and hashes, never source, evidence, argv,
environment values, or outputs. `JsonlAuditSink` uses a fixed-size set of
in-process lock stripes instead of an unbounded per-path lock cache;
multi-process writers still require an external logging system or lock.
Asynchronous filters and execution wrappers offload the audit sink's synchronous
`emit()` call to a worker thread so file I/O does not block the event loop.
The standalone scanner and synchronous `SafetyGuard.check()` remain
synchronous. An audit sink failure is logged by exception type and does not
replace the scanner's allow, review, or deny decision. OpenTelemetry failures
are also isolated.

## Security boundary

This scanner is a defense-in-depth control, not a sandbox. Static analysis
cannot observe actual syscalls or reliably resolve all generated, obfuscated, or
indirect code. Use filesystem permissions, network isolation, process and memory
limits, runtime timeouts, dependency controls, and a sandbox for untrusted
execution.

`GuardedProgramRunner` and `GuardedCodeExecutor` both enforce the policy timeout,
request cooperative cancellation when it expires, and propagate outer
cancellation to the delegate. They consume late task failures after cancellation
so those failures do not become unretrieved-task exceptions. A delegate may
still suppress `CancelledError` or leave child processes running, so the
cancellation request does not prove that execution terminated. Only the
concrete runtime or sandbox can guarantee termination by stopping the process,
container, or remote job.

Tool Filters protect `BaseTool.run_async()` calls. CodeExecutor does not
automatically pass through a Tool Filter, so it needs `GuardedCodeExecutor`.
Skills execute through a program runner and need `GuardedProgramRunner`.
Telemetry observes decisions but neither enforces nor overrides them.

The scanner uses closed-world allow semantics. `allow` means that every
executable call, command, wrapper, redirection, execution-environment override,
and side-effecting argument in this input was recognized and explicitly
permitted by a finite capability/profile and the active policy. It does not
mean merely that no dangerous string matched. Unconsumed arguments, unknown
options, dynamic values, unknown callables, commands without a profile, and
incomplete analysis return `needs_human_review` and stop automatic execution.
YAML is trusted configuration, but it can only decide recognized capabilities;
it cannot turn incomplete analysis or an unknown side effect into `allow`.
Policy changes should be reviewed like code changes.

The Python scanner only treats a call as known when its capability identity can
be traced to a supported import, alias, local function, or a narrow pure-call
catalog. Rebound module names, mapping-based callable lookup, unknown callbacks,
and same-named methods on third-party objects produce `PROC-UNKNOWN-001` and
stop automatic execution instead of being allowed by name alone.

Unmodeled third-party and relative Python imports also require review.
Executable Python/Bash files explicitly supplied through
`GuardedCodeExecutor.input_files` are scanned separately. Static analysis still
cannot see workspace modules that were not supplied as inputs, standard-library
module shadowing, or files generated only at runtime.

Generic Tool output limiting is opt-in because return schemas differ. The
provided adapter handles strings, bytes, string lists/tuples, or one configured
dictionary field; execution wrappers limit only their known result fields.

Explicit curl/wget configuration, header/cookie, and upload files contain
external data that the scanner cannot inspect, so they require review by
default and are denied when their paths match `paths.denied`. Python file
content sent to a network request follows the same conservative rule. The
default denied paths include common `.netrc`, `.npmrc`, `.pypirc`,
`.git-credentials`, Docker credential, `credentials.json`, and `secrets.json`
locations.
