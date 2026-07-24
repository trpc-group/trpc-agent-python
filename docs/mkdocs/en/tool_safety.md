# Tool Script Safety Guard

Tool Script Safety Guard is a **static script security scanning module** in the tRPC-Agent-Python SDK that performs static security analysis on scripts **before** they are executed by Agent Tools / Skills / CodeExecutor. It produces `Allow / Deny / NeedsReview` decisions and intercepts high-risk scripts at the front of the execution chain.

## Core Concepts

### What & Why

**Tool Script Safety Guard** is a **static pre-execution script scanner**, not a runtime sandbox. It reduces security risks by analyzing script content **before** execution through:

- **Static Analysis**: Detects dangerous patterns (dangerous file operations, network egress, sensitive information leakage) without running the script
- **Zero Intrusion**: Integrates via Filter or Wrapper without modifying core source code, fully backward compatible
- **Dual Language Support**: Supports both Python (AST + import-as alias tracking) and Bash (shlex + quote state machine)
- **Conservative Policy**: Defaults to conservative decision-making, tending to block rather than allow uncertain cases

**Important**: This mechanism is "pre-execution static policy judgment" and **cannot replace sandbox isolation**. Runtime resource limits and environment isolation must still rely on the CodeExecutor's container or sandbox mechanisms. This is exactly why we chose the wrapper approach without modifying core source code.

### Quick Start

#### Method 1: Using `ToolSafetyFilter` to Intercept Tool/Skill Execution

```python
from trpc_agent_sdk.tools.safety import ToolSafetyFilter

# Add safety filter when defining a Tool
class MyTool(BaseTool):
    # Method 1: Attach via filters_name parameter
    filters_name = ["tool_safety"]  # Auto-registered filter name
    
    # Method 2: Dynamically attach via add_one_filter
    def __init__(self):
        super().__init__()
        self.add_one_filter("tool_safety")
    
    async def _run_async_impl(self, **kwargs):
        # When kwargs contains script/code/command fields
        # Safety scan runs first, only executes here if decision is ALLOW
        code = kwargs.get("code", "")
        return await execute_some_script(code)
```

#### Method 2: Using `SafetyGuardedCodeExecutor` to Wrap CodeExecutor

```python
from trpc_agent_sdk.code_executors import UnsafeLocalCodeExecutor
from trpc_agent_sdk.tools.safety import SafetyGuardedCodeExecutor

# Wrap any CodeExecutor, automatically scan each code block
original_executor = UnsafeLocalCodeExecutor()
guarded_executor = SafetyGuardedCodeExecutor(
    delegate=original_executor,
    block_on_review=True  # Whether to block NEEDS_REVIEW decisions, defaults to True
)

# Use guarded_executor, dangerous code blocks will be skipped
result = await guarded_executor.execute_code(
    invocation_context=context,
    input_data=CodeExecutionInput(
        code="import os; os.system('rm -rf /')",  # Dangerous code
        language="python"
    )
)
# result.stderr will contain "TOOL_SAFETY_BLOCKED [python] DENY (...)"
```

### rule_id Domain Mapping

The system covers **7 major risk domains** with 20 rules (aligning with `trpc-agent-go/tool/safety` style):

| Domain Prefix | Risk Coverage | Example rule_ids |
|---------------|---------------|------------------|
| `tool-code-*` | Code execution | `tool-code-unsafe-eval`, `tool-code-unsafe-exec`, `tool-code-unsafe-import` |
| `tool-fs-*` | Dangerous file operations | `tool-fs-recursive-delete`, `tool-fs-read-credentials`, `tool-fs-system-dir-write` |
| `tool-net-*` | Network egress | `tool-net-http`, `tool-net-socket` |
| `tool-proc-*` | Process/system commands | `tool-proc-subprocess`, `tool-proc-shell-pipe`, `tool-proc-privilege-escalation` |
| `tool-pkg-*` | Dependency installation | `tool-pkg-install` (pip/npm/apt) |
| `tool-res-*` | Resource abuse | `tool-res-infinite-loop`, `tool-res-fork-bomb`, `tool-res-long-sleep`, `tool-res-large-write`, `tool-res-concurrent-flood` |
| `tool-secret-*` | Sensitive information leakage | `tool-secret-logging`, `tool-secret-private-key` |

### Decision and RiskLevel

#### Decision

```python
class Decision(IntEnum):
    UNDECIDED = 0      # Undecided (rule not covered)
    ALLOW = 1          # Allow execution
    DENY = 2           # Deny execution
    NEEDS_REVIEW = 3   # Needs manual review
```

#### RiskLevel

```python
class RiskLevel(IntEnum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
```

#### Decision Aggregation Logic (Conservative Policy)

The system uses **dual-track judgment**: rule-level judgment priority + policy threshold fallback.

1. **Any Finding with `rule_decision == DENY`**, or `risk_level >= policy.deny_risk_level` → `Decision.DENY`
2. **Otherwise any `rule_decision == NEEDS_REVIEW`**, or `risk_level >= policy.review_risk_level` → `Decision.NEEDS_REVIEW`
3. **Otherwise** → `Decision.ALLOW`

Default thresholds:
- `deny_risk_level: HIGH`        # Risk ≥ HIGH → DENY
- `review_risk_level: MEDIUM`    # Risk ≥ MEDIUM → NEEDS_REVIEW

### Policy Configuration

Configure policies via YAML file. **Editing the configuration file changes behavior without modifying code** (satisfying issue acceptance #6).

#### Configuration File Location

Specify the path via environment variable `TRPC_AGENT_TOOL_SAFETY_POLICY`. If not specified, the built-in default policy is used:

```bash
export TRPC_AGENT_TOOL_SAFETY_POLICY=/path/to/custom_policy.yaml
```

#### YAML Field Explanations

```yaml
name: default
description: Default tool script safety policy for tRPC-Agent.

# Risk level thresholds (used when rule's decision is UNDECIDED)
deny_risk_level: HIGH        # findings >= HIGH -> DENY
review_risk_level: MEDIUM    # findings >= MEDIUM (and < deny) -> NEEDS_REVIEW

# Global whitelists
whitelisted_domains:         # Network whitelist domains
  - pypi.org
  - github.com
  - example.com
allowed_commands:            # Allowed commands
  - ls
  - cat
  - echo
  - python
denied_paths:                # Denied access paths
  - /etc
  - /root
  - ~/.ssh
  - ~/.env
  - ~/.aws/credentials

# Resource limits (informational for static scan; enforced by executor runtime)
max_timeout_seconds: 30
max_output_bytes: 1048576
max_evidence_chars: 200      # Maximum evidence fragment length

# Rule-level overrides (optional)
rule_overrides:
  # tool-net-http:
  #   risk_level: HIGH
  #   decision: DENY
```

### Relationship with Other Components

#### Relationship with Sandbox

**Tool Script Safety Guard cannot replace sandbox isolation**:

- **This mechanism**: Static analysis, intercept **before** execution, based on pattern matching
- **Sandbox**: Runtime isolation, restrict **during** execution, based on resource/permission control
- **Complementarity**: Static interception reduces sandbox escape risk; sandbox prevents actual damage from statically missed code

**Why it cannot replace sandbox**:
1. Inherent static analysis limitations: obfuscation/encoding bypasses (`base64 -d | sh`), dynamic concatenation, indirect calls can leak
2. Runtime behavior is unpredictable: in-memory code injection, reflection calls cannot be statically detected
3. Resource abuse requires runtime limits: infinite loops, memory exhaustion need timeout/resource quota control

#### Relationship with Filter

`ToolSafetyFilter` is a subclass of `BaseFilter`, registered as `"tool_safety"`:

- **Execution timing**: **Before** Tool's `_run_async_impl`
- **Interception behavior**: When decision is not `ALLOW`, returns `FilterResult(is_continue=False)`, does not call `handle()`
- **Applicable scenarios**: Intercept single Tool/Skill execution

#### Relationship with CodeExecutor

`SafetyGuardedCodeExecutor` is a wrapper for `BaseCodeExecutor`:

- **Execution timing**: **Before** CodeExecutor's `execute_code`
- **Interception behavior**: Scan each CodeBlock individually, skip dangerous blocks, only execute safe blocks
- **Applicable scenarios**: Protect any CodeExecutor (including `UnsafeLocalCodeExecutor`)

#### Relationship with Telemetry

- **Audit log**: Every scan appends one JSON Lines audit record to `tool_safety_audit.jsonl` (path configurable via env `TRPC_AGENT_TOOL_SAFETY_AUDIT`), containing all issue #90 mandatory fields: `tool_name`, `decision`, `risk_level`, `rule_ids`, `scan_duration_ms`, `sanitized`, `intercepted`, `timestamp`, `recommendation`. Allowed scripts are summarized; denied scripts record the block reason. See `trpc_agent_sdk/tools/safety/examples/tool_safety_audit.jsonl`.
- **OpenTelemetry**: When the host has OTel enabled and a scan runs inside a span, it automatically sets span attributes `tool.safety.decision` / `tool.safety.risk_level` / `tool.safety.rule_id` / `tool.safety.scan_duration_ms` / `tool.safety.sanitized` / `tool.safety.blocked` / `tool.safety.tool_name` (no-op when OTel is absent or no span is active).
- **Structured report**: See `trpc_agent_sdk/tools/safety/examples/tool_safety_report.json`; the CLI `scripts/tool_safety_check.py` prints exactly this JSON shape.

### Known Limitations

1. **Inherent Static Analysis Limitations**
   - Obfuscation/encoding bypasses: `base64 -d | sh`, `eval(base64.b64decode("..."))` can leak
   - Dynamic concatenation: `getattr(os, "system")("rm -rf /")`, indirect calls may leak
   - Runtime code injection: In-memory modifications, `__import__` dynamic loading cannot be statically detected

2. **False Positives and False Negatives**
   - **False positives**: Legitimate scripts match dangerous patterns (e.g., legitimate `subprocess.call` flagged)
   - **False negatives**: New bypass techniques not covered (e.g., new obfuscation methods)
   - **Tuning methods**: Adjust via `whitelisted_domains`, `allowed_commands`, `rule_overrides`

3. **Bash Parsing is Heuristic**
   - Uses `shlex` + state machine, not a complete POSIX shell parser
   - Complex quote/escape boundaries may be misjudged (e.g., `$'..."..."'` nesting)

4. **Python AST Parsing Failures**
   - Falls back to string heuristics when AST parsing fails (logs but does not block)
   - Depends on syntax correctness; syntax-error scripts may bypass detection

### Extending Rules

Adding new rules requires modifications in two places:

#### 1. Add Constants in `_rules.py`

```python
# trpc_agent_sdk/tools/safety/_rules.py

# Add new rule ID
R_MY_CUSTOM_RULE = "tool-custom-my-rule"

# Define default behavior in DEFAULT_RULE_POLICIES
DEFAULT_RULE_POLICIES: dict[str, tuple[RiskLevel, Decision]] = {
    # ... existing rules ...
    R_MY_CUSTOM_RULE: (RiskLevel.MEDIUM, Decision.NEEDS_REVIEW),
}
```

#### 2. Add Detection Logic in Scanner

**Python Scanner** (`_python_scanner.py`):

```python
# Add detection branch in scan function
def _scan_python_script(policy: Policy, script: str) -> list[Finding]:
    findings = []
    # ... existing detection logic ...
    
    # Add new detection
    if "dangerous_pattern" in script:
        findings.append(Finding(
            rule_id=R_MY_CUSTOM_RULE,
            risk_level=RiskLevel.MEDIUM,
            rule_decision=Decision.NEEDS_REVIEW,
            evidence="...",
            recommendation="...",
            language="python"
        ))
    
    return findings
```

**Bash Scanner** (`_bash_scanner.py`):

```python
# Add detection branch in Bash scan function
def _scan_bash_script(policy: Policy, script: str) -> list[Finding]:
    findings = []
    # ... existing detection logic ...
    
    # Add new detection
    if "dangerous_command" in tokens:
        findings.append(Finding(
            rule_id=R_MY_CUSTOM_RULE,
            risk_level=RiskLevel.MEDIUM,
            rule_decision=Decision.NEEDS_REVIEW,
            evidence="...",
            recommendation="...",
            language="bash"
        ))
    
    return findings
```

#### 3. Override in YAML (Optional)

```yaml
# tool_safety_policy.yaml
rule_overrides:
  tool-custom-my-rule:
    risk_level: HIGH
    decision: DENY
```

### References

- **Design Document**: `docs/superpowers/specs/2026-07-09-tool-safety-guard-design.md`
- **Implementation Code**: `trpc_agent_sdk/tools/safety/`
- **Test Cases**: `tests/tools/safety/samples/manifest.yaml`
- **Corresponding Issue**: [trpc-group/trpc-agent-python#90](https://github.com/trpc-group/trpc-agent-python/issues/90)
