# Issue #90 Design Document: Tool Safety Scanner

> **Author**: coder-mtj
> **Reference**: [trpc-agent-go/tool/safety](https://github.com/trpc-group/trpc-agent-go) (PR #2091, merged)

## Overview

The Tool Safety Scanner provides a **pre-execution safety guard** for command and
code-execution tools in the tRPC Agent Python SDK. Before any `workspace_exec`,
`exec_command`, or `execute_code` invocation reaches the underlying runtime, the
scanner evaluates the request against a configurable policy and produces a
decision: **allow**, **deny**, **ask for confirmation**, or **needs human review**.

## Architecture

```
                         Tool Invocation
                               │
                               ▼
                    ┌─────────────────────┐
                    │  ToolSafetyFilter    │  ← FilterABC (FilterType.TOOL)
                    │  _before() hook      │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  scan(request,       │
                    │       policy)        │
                    │       → Report       │
                    └──────────┬──────────┘
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
   │ Envelope Scan │   │  Env Scan    │   │ Shell/Code   │
   │ (cwd, timeout, │   │ (allowlist,  │   │ Block Scan   │
   │  background)   │   │  secrets)    │   │ (commands,   │
   └──────────────┘   └──────────────┘   │  URLs, pipes) │
                                          └──────────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  Worst Finding →     │
                    │  Report + Redaction  │
                    └─────────────────────┘
```

## Module Structure

```
trpc_agent_sdk/tools/safety/
├── __init__.py        # Public API exports
├── _types.py          # Decision, RiskLevel, Policy, Request, Finding, Report, AuditEvent
├── _policy.py         # default_policy(), load_policy(YAML/JSON)
├── _shell_parse.py    # Pure-Python command/URL/pipeline parser (no subprocess)
├── _redactor.py       # Secret detection and redaction
├── _scanner.py        # Core scan engine: scan(request, policy) → Report
└── _permission.py     # ToolSafetyFilter: FilterABC integration for tool interception

tests/tools/safety/
├── __init__.py
├── test_types.py      # 16 unit tests for data types
└── test_scanner.py    # 18 scan tests (12 core + 6 edge)
```

## Design Decisions

### 1. Alignment with Go Reference Implementation

The Python implementation mirrors the type system, rule IDs, and scan flow of
`trpc-agent-go/tool/safety/` (PR #2091). This ensures consistent behavior across
SDKs and makes cross-referencing easy for contributors.

### 2. Pure Python Static Analysis

Shell command parsing in `_shell_parse.py` never invokes a subprocess. It uses
regex patterns and a character-by-character state machine (for quote-aware
pipeline detection). This eliminates the attack surface of calling out to a real
shell during safety evaluation.

### 3. FilterABC Integration

The scanner is integrated as a `FilterABC` subclass (`ToolSafetyFilter`), which
hooks into the existing tRPC filter chain at the TOOL level. When `_before()`
returns a DENY decision, `rsp.is_continue = False` halts the chain — the tool
never executes. This is more flexible than a decorator-based approach because it
supports filter composition and ordering.

### 4. Secret Redaction

After scanning, the `Redactor` class runs across all text fields in the Report
and Findings. This prevents API keys, tokens, and credentials from leaking into
logs or audit trails. The `report.redacted` flag tracks whether any substitution
occurred.

### 5. Layered Policy

`default_policy()` provides conservative defaults suitable for most use cases.
`load_policy(path)` reads a YAML/JSON file and overlays values on top of
defaults, allowing teams to customize without starting from scratch.

### 6. Finding Priority

`finding_beats(a, b)` compares two findings by decision rank first (DENY > ASK >
NEEDS_HUMAN_REVIEW > ALLOW), then by risk rank (CRITICAL > HIGH > MEDIUM > LOW).
The "worst" finding drives the final Report decision.

## Risk Categories (6 categories)

| Category | Rule ID Prefix | Examples |
|----------|---------------|----------|
| Dangerous Commands | `dangerous.*` | `rm -rf /`, `chmod -R` |
| Sensitive Information | `sensitive.*` | `path_access`, `secret_leak`, `cwd_access` |
| Network Egress | `network.*` | `non_whitelisted_domain` |
| Shell Bypass | `shell.*` | `bypass`, `pipeline_review` |
| Resource Abuse | `resource.*` | `long_sleep`, `infinite_loop`, `timeout_exceeded` |
| Dependency Changes | `dependency.*` | `environment_change` |

## Scan Flow

```
1. Envelope Scan
   ├── Check CWD against denied_paths
   ├── Detect hostexec long sessions (TTY + background)
   ├── Validate timeout against policy.max_timeout_seconds
   └── Validate output cap against policy.max_output_bytes

2. Environment Scan
   ├── Verify each env key is in env_allowlist
   └── Scan env values for secrets

3. Shell/Code Block Scan
   ├── Raw command: secrets → bypass → pipeline → background → network → resources
   ├── Per-command: denied command → dangerous patterns → review commands → denied paths
   └── Code blocks: secrets → language routing (shell vs. other) → bridge detection

4. Report Assembly
   ├── Pick worst finding via finding_beats()
   ├── Set blocked flag
   ├── Redact all text fields
   └── Record duration_ms
```

## Dependencies

- `PyYAML` — policy file parsing
- `trpc_agent_sdk.abc.FilterABC` — filter base class
- Standard library: `re`, `urllib.parse`, `dataclasses`, `enum`, `json`, `pathlib`
