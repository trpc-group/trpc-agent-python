# Tool Script Safety Guard

The Tool Script Safety Guard statically scans explicit Python, Shell, or structured-command fields before a configured execution boundary. It returns one of `allow`, `deny`, or `needs_human_review`. Version 1 blocks both deny and review decisions.

It is a preflight layer, not a sandbox. An `allow` result means only that the configured static rules and policy did not block this source. Keep OS permissions minimal and use a suitable container or remote sandbox for untrusted execution.

## Install and public API

The guard uses existing project dependencies. Import its public API only from `trpc_agent_sdk.safety`; it is intentionally not re-exported from the package root.

```python
from trpc_agent_sdk.safety import PolicyLoader
from trpc_agent_sdk.safety import SafetyScanRequest
from trpc_agent_sdk.safety import SafetyScanner

loader = PolicyLoader("examples/tool_safety/tool_safety_policy.yaml")
policy = loader.load()
scanner = SafetyScanner(policy)
report = scanner.scan(
    SafetyScanRequest(
        script="print('hello')",
        language="python",
        source_type="application",
    )
)
print(report.decision.value)
```

Raw source, environment values, argv, and unrestricted metadata are excluded from the request's default representation and dump. Reports contain a SHA-256 source identifier and bounded, redacted evidence, not a source preview. A hash is an identifier, not encryption or redaction.

## Modify policy without changing code

The YAML schema requires `schema_version: "1"`, rejects unknown and duplicate keys, and rejects anchors, aliases, and merge keys. It supports domain/path/command lists, rule enablement and restricted overrides, risk thresholds, fail-closed actions, nested budgets, redaction limits, Audit enablement declarations, and runtime-only declarations. Redaction cannot be disabled in version 1.

```python
loader = PolicyLoader("policy.yaml")
scanner = SafetyScanner(loader.load())

# Explicit administrative action. A failed reload leaves the old snapshot active.
new_policy = loader.reload()
scanner = SafetyScanner(loader)
```

The Scanner never rereads policy files on the scan hot path. One root scan and all nested scans retain the same immutable policy version/hash. `block_on_review` is fixed to `true` in version 1. Ordinary allowlists cannot override secret exfiltration, protected-root destruction, fork bombs, or explicit download-and-execute findings.

`allowed_commands` is enforced as a whitelist for structured `argv` boundaries such as `SafetyProgramRunner`; unlisted commands require review. Free-form Shell still receives semantic command rules rather than a blanket whitelist. `allowed_paths` is reserved policy metadata in version 1 and does not make a path trusted or override any finding.

`runtime_limits.enforcement` must be `declaration_only`. Timeout, CPU, memory, PID, network, and filesystem limits require an executor or sandbox integration; the static Scanner does not enforce them.

## Python and Shell analysis

Python source is parsed once with `ast.parse`. Shared facts include import/from-import aliases, simple shadowing, calls and keyword arguments, known/partially-known/unknown values, constant concatenation, static f-string portions, limited object origins, and limited source-to-sink flow. Rules cover sensitive files, file mutation, network targets, processes, dynamic execution, dependency installation, resource patterns, and statically recoverable nested scripts.

Shell source is parsed once by a quote/operator-aware conservative lexer. It retains quotes, escapes, groups, pipelines, environment prefixes, ordered redirections, wrappers, command substitution, heredocs, and nested `-c` payloads. `shlex` is used only when converting already-structured argv; it is not described as a complete Bash parser.

Syntax errors, unsupported languages, dynamic security-relevant values, and exceeded budgets do not silently allow execution. Default policy maps incomplete analysis to blocked review and internal scanner failure to deny.

## Tool Filter integration

The Filter requires an explicit extractor. This prevents arbitrary business strings from being treated as scripts.

```python
from trpc_agent_sdk.safety import ToolArgumentExtractor
from trpc_agent_sdk.safety import ToolSafetyFilter

safety_filter = ToolSafetyFilter(
    scanner,
    ToolArgumentExtractor(
        script_field="script",
        language_field="language",
        cwd_field="cwd",
        env_field="env",
    ),
)

# Add safety_filter to a BaseTool/FunctionTool's filters list.
```

The filter scans repaired Tool args before `BaseTool._run_async_impl`. Allow calls the real Tool exactly once and preserves its result. Deny/review calls it zero times and returns a minimal redacted `{"safety": ...}` envelope. Upstream Tool response construction retains the existing function-call id pairing. Per-call reports are not stored in mutable Filter fields.

## CodeExecutor wrapper integration

```python
from trpc_agent_sdk.safety import SafetyCodeExecutor

safe_executor = SafetyCodeExecutor(inner=existing_executor, scanner=scanner)
# Pass safe_executor as LlmAgent(code_executor=...).
```

Every code block is scanned before any delegation. One deny or review blocks the complete batch, and the inner executor is not called. An all-allow batch delegates once with the original `CodeExecutionInput` and returns the exact original `CodeExecutionResult` object. A block returns the existing type with `OUTCOME_FAILED`, the original execution id, and a minimal JSON safety envelope in `output`; details can be received with `SafetyScanner(report_observers=(callback,))`. Inner backend exceptions remain backend exceptions and are not disguised as policy decisions.

## Callable, Skill, Workspace, and MCP integration

`SafetyCallable` supports synchronous and asynchronous callables with an explicit request factory. The complete non-executing example is in [examples/tool_safety](../../../examples/tool_safety/).

Skills are workflow packages, not a unified executor. Protect the real FunctionTool, CodeExecutor, Workspace runner, or MCP Tool used by the Skill. `SafetyProgramRunner` wraps only `BaseProgramRunner.run_program`; allow forwards the same `WorkspaceRunProgramSpec`, while a block returns exit code 126 without calling the inner runner. It deliberately does not invent or wrap an optional `start_program` capability.

`SafetyMCPAdapter` wraps a ClientSession-like `call_tool` with per-tool extractors. Tools without an extractor pass business arguments through unchanged. Client-side scanning cannot see MCP Server internals, discovery calls, follow-up actions, or stdio Server startup.

Framework-controlled Skill staging and model-controlled Workspace commands must be distinguished by the integrating application; globally wrapping every Workspace command may otherwise block trusted framework plumbing.

## Audit, redaction, monitoring, and OpenTelemetry

```python
from pathlib import Path
from tempfile import gettempdir

from trpc_agent_sdk.safety import JsonlAuditSink
from trpc_agent_sdk.safety import OpenTelemetrySafetySink

audit = JsonlAuditSink(Path(gettempdir()) / "tool-safety-audit.jsonl")
scanner = SafetyScanner(
    policy,
    audit_sink=audit,
    telemetry_sink=OpenTelemetrySafetySink(),
)
```

Allow, deny, and review all create an immutable observation after the decision. One bounded recursive sanitizer runs before fan-out and handles mappings, containers, dataclasses, Pydantic models, bytes, paths, enums, exceptions, cycles, sensitive keys, and size limits. Raw script, complete env/argv/output, evidence, URL, path, and command are not sent to sinks.

JSONL uses UTF-8, one event per line, `allow_nan=False`, and an in-process lock. Use one file per process or an external writer; multi-process append atomicity is not promised. The reader can ignore one partial final record. Sink failures retain the safety decision and produce a redacted health signal for remaining monitors.

The optional OpenTelemetry sink follows the repository's `trpc.python.agent` instrumentation scope. It emits a `safety.scan` event and safety count/duration metrics with low-cardinality `safety.*` fields only. Missing providers, missing active spans, or reporter/export failures never alter the decision.

## Add a new safety rule

Subclass `SafetyRule`, assign a stable namespaced `rule_id`, keep the object stateless, and return immutable `SafetyFinding` values. A rule may only read the supplied context and Policy. It must not execute source, access the network, read real files, write audit data, or decide the entire report.

```python
from trpc_agent_sdk.safety import RiskLevel
from trpc_agent_sdk.safety import SafetyCategory
from trpc_agent_sdk.safety import SafetyFinding
from trpc_agent_sdk.safety import SafetyRule

class OrganizationRule(SafetyRule):
    rule_id = "ORG.PROCESS.EXAMPLE"
    languages = ("python",)

    def evaluate(self, context, policy):
        del policy
        if "organization_specific_call(" not in context.source:
            return ()
        return (
            SafetyFinding(
                rule_id=self.rule_id,
                category=SafetyCategory.PROCESS,
                risk_level=RiskLevel.MEDIUM,
                message="Organization-specific operation requires review.",
                evidence="<organization operation>",
                recommendation="Obtain approval.",
            ),
        )

scanner = SafetyScanner(policy, rules=(OrganizationRule(),))
```

Rule exceptions are converted to fail-closed internal diagnostics. Findings are stably sorted and deduplicated; Rule registration order does not change decision precedence.

## CLI and exit codes

The CLI only reads and scans source:

```bash
.venv/bin/python scripts/tool_safety_check.py script.py \
  --policy examples/tool_safety/tool_safety_policy.yaml --json

.venv/bin/python scripts/tool_safety_check.py --stdin --language shell --json

.venv/bin/python scripts/tool_safety_check.py \
  --manifest examples/tool_safety/sample_manifest.yaml \
  --policy examples/tool_safety/tool_safety_policy.yaml --json
```

| Exit | Meaning |
| --- | --- |
| 0 | allow, or every manifest expectation matched |
| 2 | deny |
| 3 | needs_human_review |
| 4 | invalid input or policy |
| 5 | operational failure |
| 6 | manifest expectation mismatch |

The public manifest covers 12 named examples. The separate deterministic corpus is used for detection and false-positive metrics; public demonstrations are not reused as the metric denominator.

## False positives, false negatives, and bypass risks

Dynamic Python reflection, complex control/data flow, runtime monkey patching, native extensions, complex Shell expansion/functions/arrays/process substitution, and sourced runtime content may be incomplete. Conservative review can cause false positives; unknown behavior, obfuscation, encoding, alternate binaries, parser differentials, and behavior hidden behind external services can cause false negatives or bypasses.

Do not describe corpus results as universal detection rates. Keep policies versioned, monitor scanner health, test organization-specific scripts, use least privilege, and enforce runtime restrictions independently.

## Security non-goals and known limitations

- No runtime CPU, memory, PID, network, filesystem, syscall, privilege, or secret isolation.
- No guarantee that an allowed script is harmless.
- No complete Python semantic interpreter or complete Bash parser.
- No automatic coverage of all Skill, Workspace, MCP discovery/startup, or MCP Server internals.
- No human approval/resume workflow in version 1; review is blocked.
- No multi-process JSONL writer guarantee.

The guard complements Filter interception, CodeExecutor/Workspace boundaries, sandboxing, and telemetry; none replaces the others.
