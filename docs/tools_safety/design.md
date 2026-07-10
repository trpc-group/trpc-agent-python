# Tool Script Safety Guard — Design Document

## Overview

The Tool Script Safety Guard is a security mechanism that scans tool-executed scripts (Python, Bash) for risks **before** execution. It integrates into tRPC-Agent's existing filter pipeline, provides a standalone scanning API, and emits structured audit records and OpenTelemetry span attributes.

### Scope

- Scan Python scripts and Bash commands for 6 risk categories
- Pluggable as a `BaseFilter` in the tool execution chain
- Configurable via `tool_safety_policy.yaml` without code changes
- Output structured scan reports and JSONL audit logs
- Decorate existing OpenTelemetry spans with safety metadata

### Out of Scope

- Runtime sandboxing (handled by `ContainerCodeExecutor`, `CubeCodeExecutor`)
- Network-level firewalling or egress control
- Replacement for proper container/VM isolation

---

## Package Structure

```
trpc_agent_sdk/tools/safety/
├── __init__.py            # Public exports
├── _types.py              # Enums, dataclasses (RiskType, Decision, ScanReport, AuditEvent)
├── _policy.py             # SafetyPolicy model, YAML loading/validation
├── _rules.py              # Rule definitions: PatternRule, AstRule (~14 rules)
├── _scanner.py            # ToolSafetyScanner — orchestration and scan pipeline
├── _filter.py             # ToolSafetyFilter(BaseFilter) — filter integration
├── _audit.py              # SafetyAuditLogger — JSONL audit logging
├── _telemetry.py          # set_safety_span_attrs() — OTel span decoration
└── tool_safety_policy.yaml  # Default policy file
```

---

## Data Model

### Enums

| Enum | Values | Description |
|------|--------|-------------|
| `RiskType` | `dangerous_file_operation`, `network_access`, `system_command`, `dependency_install`, `resource_abuse`, `sensitive_info_leak` | 6 risk categories |
| `Decision` | `allow`, `deny`, `needs_human_review` | Outcome of a safety scan |
| `RiskLevel` | `low`, `medium`, `high`, `critical` | Severity of a finding |

### Dataclasses

**RuleFinding** — A single rule match:
- `rule_id: str` — e.g. `"DANGEROUS_DELETE_001"`
- `risk_type: RiskType`
- `risk_level: RiskLevel`
- `evidence: str` — matched script line/snippet
- `message: str` — human-readable description
- `recommendation: str` — suggested mitigation

**ScanReport** — Aggregated scan result:
- `decision: Decision` — worst-case decision across all findings
- `risk_level: RiskLevel | None`
- `findings: list[RuleFinding]`
- `scan_duration_ms: float`
- `script_snippet: str | None` — first N chars for context
- `scan_error: str | None`

**AuditEvent** — For JSONL logging:
- `timestamp: str` (ISO 8601)
- `tool_name: str`
- `decision: str`
- `risk_level: str | None`
- `rule_ids: list[str]`
- `scan_duration_ms: float`
- `sanitized: bool`
- `intercepted: bool`
- `script_hash: str` (SHA-256)

---

## Policy Configuration

`tool_safety_policy.yaml` controls all configurable behavior:

```yaml
version: "1.0"
max_script_size_bytes: 1048576    # 1 MB
max_scan_time_ms: 1000            # 1 second
default_decision: deny

rules:
  - rule_id: DANGEROUS_DELETE_001
    enabled: true
    risk_type: dangerous_file_operation
    severity: critical
    decision: deny
  - rule_id: SENSITIVE_PATH_002
    enabled: true
    risk_type: dangerous_file_operation
    severity: critical
    decision: deny
  # ...

whitelist:
  domains: ["api.example.com", "trusted.internal.org"]
  commands: ["ls", "cat", "echo", "pwd"]
  paths: ["/tmp/", "/workspace/", "./"]

blocklist:
  paths: ["~/.ssh", "~/.aws", "/etc/passwd", "/etc/shadow", ".env"]
  commands: ["sudo", "chmod 777"]
```

### Policy behavior rules:

1. Whitelist overrides blocklist — an item in both is allowed
2. `default_decision` is used when no rules match (conservative: `deny`)
3. Rules can be individually enabled/disabled via `enabled: false`
4. Severity and decision are overridable per-rule
5. `max_scan_time_ms` acts as hard timeout; timeout → `default_decision`

---

## Risk Categories and Rules

### 1. Dangerous File Operations (`dangerous_file_operation`)

| Rule ID | Triggers | Detection |
|---------|----------|-----------|
| `DANGEROUS_DELETE_001` | `rm -rf`, `shutil.rmtree()`, `os.remove()` on non-/tmp paths | Pattern + AST |
| `SENSITIVE_PATH_002` | Access to `~/.ssh`, `/etc/passwd`, `~/.aws`, `.env`, `~/.config` | Pattern |

### 2. Network Access (`network_access`)

| Rule ID | Triggers | Detection |
|---------|----------|-----------|
| `NETWORK_CURL_003` | `curl`, `wget` in Bash scripts | Pattern + domain whitelist check |
| `NETWORK_PYTHON_004` | `requests.get/post`, `httpx.get/post`, `urllib.request` | Pattern + AST |
| `NETWORK_SOCKET_005` | `socket.connect()`, `socket.create_connection()` | Pattern + AST |

### 3. System and Process Commands (`system_command`)

| Rule ID | Triggers | Detection |
|---------|----------|-----------|
| `SUBPROCESS_006` | `subprocess.run/Popen/call` | Pattern + AST |
| `OS_SYSTEM_007` | `os.system()`, `os.popen()`, backtick execution in Bash | Pattern |
| `PRIVILEGE_ESCALA_009` | `sudo`, `su`, `chmod 777`, `chown` | Pattern |

### 4. Dependency Installation (`dependency_install`)

| Rule ID | Triggers | Detection |
|---------|----------|-----------|
| `DEP_INSTALL_008` | `pip install`, `npm install`, `apt-get install`, `yum install` | Pattern |

### 5. Resource Abuse (`resource_abuse`)

| Rule ID | Triggers | Detection |
|---------|----------|-----------|
| `FORK_BOMB_011` | `:(){ :\|:& };:`, mass `fork()` calls | Pattern + AST |
| `INFINITE_LOOP_012` | `while True:`, `while (true)`, `for(;;)` | Pattern + AST |

### 6. Sensitive Information Leak (`sensitive_info_leak`)

| Rule ID | Triggers | Detection |
|---------|----------|-----------|
| `SENSITIVE_LOG_010` | `print(api_key)`, `write(token)`, env vars named `*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*` | Pattern + AST |

---

## Scan Pipeline

```
script_text, tool_name, args, env_vars
         │
         ▼
  1. PRE-CHECK —— size > max_script_size_bytes? → deny
         │
         ▼
  2. WHITELIST FAST PATH —— all detected domains/commands/paths in whitelist?
         │                                       → allow (skip full scan)
         ▼
  3. PATTERN SCAN —— apply all enabled pattern rules against text, args, env_vars
         │
         ▼
  4. AST SCAN —— if Python detected, parse AST, apply AST rules
         │
         ▼
  5. AGGREGATE —— combine findings, resolve highest severity → decision
         │
         ▼
  6. AUDIT —— emit ScanReport + audit event
         │
         ▼
      return ScanReport
```

### Decision aggregation

```
worst_decision = max(findings, key=severity_priority)
```

Priority: `DENY > NEEDS_HUMAN_REVIEW > ALLOW`. A single `DENY` finding blocks execution regardless of other findings.

### Python detection heuristic

The scanner checks for: `def `, `import `, `from `, `class `, `#!python`. If none of these are present, the AST scan step is skipped and only pattern rules apply. Malformed Python that fails `ast.parse()` is caught gracefully and pattern results are still returned.

### Timeout

`asyncio.wait_for()` wraps the pattern + AST scan phases. If the timeout (`max_scan_time_ms`) is exceeded, the scan returns `default_decision` with an error note.

---

## Integration Points

### 1. Filter mode

`ToolSafetyFilter` extends `BaseFilter` and is registered for `FilterType.TOOL`. It implements `_before()` to scan tool arguments before execution and sets `rsp.is_continue = False` on deny.

```python
scanner = ToolSafetyScanner("tool_safety_policy.yaml")
register_filter(FilterType.TOOL, "tool_safety", ToolSafetyFilter(scanner, audit_logger))

# Attach to a tool
tool = FunctionTool(func=..., filters=["tool_safety"])
```

### 2. Standalone mode

```python
scanner = ToolSafetyScanner("tool_safety_policy.yaml")
report = await scanner.scan(
    script="rm -rf /home/user/data",
    tool_name="bash_tool",
)
if report.decision == Decision.DENY:
    raise SafetyViolationError(report)
```

### 3. OpenTelemetry

When an active span exists (e.g., the tool execution span created in `ToolsProcessor._execute_tool()`), the safety filter sets these attributes:

| Attribute | Type | Description |
|-----------|------|-------------|
| `tool.safety.decision` | string | `allow`, `deny`, or `needs_human_review` |
| `tool.safety.risk_level` | string | `low`, `medium`, `high`, or `critical` |
| `tool.safety.rule_ids` | string[] | IDs of all triggered rules |
| `tool.safety.scan_duration_ms` | float | Scan duration in milliseconds |

No new spans are created — existing tool spans are decorated with safety metadata.

---

## Audit Logging

Writes one JSON object per line to `tool_safety_audit.jsonl`:

```json
{"timestamp":"2026-07-10T12:00:00Z","tool_name":"bash_tool","decision":"deny","risk_level":"critical","rule_ids":["DANGEROUS_DELETE_001"],"scan_duration_ms":12.5,"sanitized":false,"intercepted":true,"script_hash":"a1b2c3..."}
{"timestamp":"2026-07-10T12:01:00Z","tool_name":"python_tool","decision":"allow","risk_level":null,"rule_ids":[],"scan_duration_ms":3.1,"sanitized":false,"intercepted":false,"script_hash":"d4e5f6..."}
```

---

## Relationship to Other Framework Components

| Component | Relationship |
|-----------|-------------|
| **CodeExecutor** (`UnsafeLocalCodeExecutor`, `ContainerCodeExecutor`, `CubeCodeExecutor`) | Safety Guard is a **pre-execution** scanner. CodeExecutors provide **runtime** isolation. The two are complementary layers: Safety Guard blocks known-dangerous scripts; the executor sandboxes scripts that pass. Safety Guard does **not** replace sandboxing. |
| **Filter system** (`BaseFilter`, `FilterRunner`, `FilterRegistry`) | `ToolSafetyFilter` is a standard filter plugged into the tool filter chain via `FilterType.TOOL`. It runs in `_before()` to inspect and potentially block execution. |
| **Telemetry** (`trace`, `metrics`) | Safety Guard decorates existing tool spans with `tool.safety.*` attributes. Does not create new spans or metrics. |
| **Callback system** (`ToolCallbackFilter`) | Safety Guard runs alongside callbacks in the same filter chain. Ordering: safety filter should run first (before callbacks) to block dangerous execution early. This is controlled by filter registration order. |

---

## Known Limitations

1. **Pattern-based detection is bypassable** — obfuscated scripts (e.g., `__import__("os").system(...)`) will evade regex rules. AST rules catch some but not all obfuscation.
2. **Bash parsing is pattern-only** — no Bash AST exists. Complex Bash scripts with variable indirection may bypass rules.
3. **False positives** — safe scripts that happen to mention dangerous keywords (e.g., a security tutorial) will be flagged.
4. **No data flow analysis** — the scanner only checks syntax, not whether a sensitive value actually flows into a dangerous call. A script that reads `API_KEY` but never outputs it will still be flagged by `SENSITIVE_LOG_010`.
5. **Not a sandbox** — this is a static analysis tool. It cannot prevent runtime exploits, memory corruption, or novel attack vectors.
6. **Whitelist fast path is conservative** — if even one element is not in the whitelist, the full scan runs. This means partially-whitelisted scripts still incur scan overhead.

---

## Extending with New Rules

Add a new rule for a new risk type:

```python
@pattern_rule(
    rule_id="NEW_RULE_013",
    risk_type=RiskType.SYSTEM_COMMAND,
    severity=RiskLevel.HIGH,
    pattern=r"evil_command\s+--dangerous",
    message="Detected use of evil_command",
    recommendation="Use safe_command instead",
)
async def check_evil_command(text: str) -> RuleFinding | None:
    ...
```

For AST rules, subclass `ast.NodeVisitor` and register with the scanner. All rules are automatically discovered by the scanner from the policy file and applied in order.

---

## File Index

| File | Purpose |
|------|---------|
| `design.md` | This document — architecture and design |
| `design.zh_CN.md` | Chinese version of this document |
| `test_plan.md` | Test cases and acceptance criteria |
| `tool_safety_policy.yaml` | Default policy configuration (in `tools/safety/`) |
| `tool_safety_report.json` | Example scan report output |
| `tool_safety_audit.jsonl` | Example audit log output |
