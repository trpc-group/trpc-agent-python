# Code Review Agent — Architecture Design

## Overview

基于 tRPC-Agent SDK 的自动化代码评审 Agent，集成 Skills、沙箱执行、SQLite 存储，提供端到端的代码审查流水线。

**核心理念**：将代码评审拆解为独立的流水线阶段，每个阶段可独立测试、替换和扩展。

## Architecture

### Pipeline (8-stage linear workflow)

```
  ┌──────────┐   ┌──────────┐   ┌───────────────┐   ┌──────────┐
  │ 1. Read  │ → │ 2. Parse │ → │ 3. Filter      │ → │ 4. Scan  │
  │   diff   │   │   diff   │   │   chain        │   │   code   │
  └──────────┘   └──────────┘   └───────────────┘   └──────────┘
       │              │                 │                  │
       ▼              ▼                 ▼                  ▼
  --diff-file    unified diff    SafetyFilter x4    10 scanners
  --repo-path    DiffFile[]      deny/allow/needs    Finding[]
  --stdin        DiffHunk[]      _human_review
       
  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
  │ 5. Sand- │ → │ 6. Dedup │ → │ 7. Report│ → │ 8. Store │
  │   box    │   │  +Redact │   │   gen    │   │   DB     │
  └──────────┘   └──────────┘   └──────────┘   └──────────┘
       │              │                 │              │
       ▼              ▼                 ▼              ▼
  Fake/Local/     fingerprint      JSON + MD       SQLite
  Workspace       + 3-tier conf    + SARIF         + schema
  SandboxRun      + 12 patterns    ReviewReport    versioning
```

### Module Map

| Module | File | Lines | Responsibility |
|--------|------|-------|----------------|
| Types | `pipeline/types.py` | ~117 | Data contracts (Finding, DiffFile, SandboxRun, etc.) |
| Config | `pipeline/config.py` | ~52 | Pipeline configuration with defaults and overrides |
| Diff Parser | `pipeline/diff_parser.py` | ~185 | Unified diff parsing, changed line extraction |
| Filter Chain | `pipeline/filter_chain.py` | ~135 | Safety filter chain with policy-as-code support |
| Scanners | `pipeline/scanners.py` | ~340 | 10 pattern-matching scanners with per-category thresholds |
| Sandbox | `pipeline/sandbox.py` | ~220 | Fake/Local/Workspace sandbox abstraction |
| Dedup | `pipeline/dedup.py` | ~110 | Fingerprint-based dedup with 3-tier confidence |
| Redaction | `pipeline/redaction.py` | ~82 | 12 regex patterns for secret/credential redaction |
| AST Analyzer | `pipeline/ast_analyzer.py` | ~230 | Python AST + JS/TS regex taint analysis |
| Report | `pipeline/report.py` | ~210 | JSON + Markdown report generation |
| SARIF Output | `pipeline/sarif_output.py` | ~120 | SARIF v2.1.0 for GitHub Code Scanning integration |
| Telemetry | `pipeline/telemetry.py` | ~78 | Timing and cost collection |
| Storage/Models | `storage/models.py` | ~72 | DB record dataclasses |
| Storage/DAO | `storage/dao.py` | ~320 | SQLite CRUD with schema versioning and migrations |
| Agent | `agent/agent.py` | ~50 | LlmAgent wrapper for tRPC-Agent framework |
| Skill | `skills/code-review/SKILL.md` | ~43 | Skill definition with rules and scripts |
| CLI | `run_review.py` | ~360 | argparse CLI entry point |
| Fixture Eval | `evaluate_fixtures.py` | ~200 | Precision/recall/F1 evaluation framework |

## Key Design Decisions

### 1. Pattern-Match Scanners vs ML-based Analysis

**Decision**: Use regex-based pattern matching with AST enhancement.
**Rationale**: 
- Deterministic and fast — no API calls, no rate limits
- Easy to audit and extend — adding a rule is one tuple
- AST analysis adds semantic understanding without complexity
- 10 scanners covering security, async, resources, DB, tests, secrets, and code quality

### 2. Three-Tier Confidence System

**Decision**: Classify findings into high (≥0.8), warning (≥0.55), needs_human_review (<0.55).
**Rationale**:
- Mirrors real production review workflows
- High-confidence findings can auto-block merges
- Warning tier gives reviewers a prioritized list
- Low-confidence items don't create noise in automated mode
- Per-scanner threshold overrides allow fine-tuning

### 3. Multi-Runtime Sandbox Abstraction

**Decision**: SandboxRunner ABC with Fake/Local/Workspace implementations.
**Rationale**:
- Fake runner enables complete CI testing without Docker
- Local runner for development with subprocess isolation
- Workspace runner for production (Container/Cube/E2B)
- Trigger-based edge case simulation in fake mode (timeout, large output, secrets)

### 4. Policy-as-Code Filter Governance

**Decision**: Externalize filter rules to `filter_policy.json`.
**Rationale**:
- Rules updateable without code changes
- Auditable — filter decisions logged to DB
- Extensible — add new patterns without redeploying
- Pre-block high-risk scripts before sandbox execution

### 5. SQLite with Schema Versioning

**Decision**: Use SQLite with COLUMN_MIGRATIONS dict for schema evolution.
**Rationale**:
- Zero-dependency storage
- Schema version table tracks current state
- Column migrations apply incrementally (v1→v2→v3→v4)
- WAL mode for concurrent reads
- Forward-compatible: new columns have defaults

### 6. Multi-Format Output (JSON + MD + SARIF)

**Decision**: Generate three output formats from the same ReviewReport.
**Rationale**:
- JSON: machine-readable, API integration
- Markdown: human-readable, PR comments
- SARIF v2.1.0: GitHub Code Scanning, Azure DevOps integration

## Failure Modes and Mitigations

| Failure Mode | Detection | Mitigation |
|-------------|-----------|------------|
| Empty diff | Stage 2: 0 files parsed → early exit | No crash, clean exit 0 |
| Malicious diff content | Stage 3: FilterChain evaluation | Block before sandbox execution |
| Sandbox timeout | Stage 5: timeout_seconds limit | SandboxRun.timed_out=True, pipeline continues |
| Sandbox crash | Stage 5: exception handling | SandboxRun.error, pipeline continues |
| Output too large | Stage 5: max_output_bytes limit | Truncation with marker |
| DB connection failure | Stage 8: exception handling | Report still written to disk before DB |
| Scanner regex catastrophic backtracking | Per-scanner execution, timeout | Individual scanner failure doesn't crash others |

## Trade-offs

| Trade-off | Choice | Why |
|-----------|--------|-----|
| Speed vs Accuracy | Prioritized speed | Regex scanners are O(n) per pattern; real LLM review can be layered on top |
| Coverage vs Precision | Balanced (3-tier) | High-confidence blocking + human review for edge cases |
| Simplicity vs Features | Leaned toward feature completeness | 10 scanners, 3 sandbox modes, 3 output formats, but each module is simple |
| Testing vs Implementation | Heavy testing investment | 205 tests across 15 files → refactoring confidence |

## Data Flow

```
diff_text (str)
  → parse_diff() → DiffFile[]
  → FilterChain.evaluate() → FilterDecision
  → run_scanners() × N → Finding[]
  → FakeSandboxRunner.run() → SandboxRun
  → deduplicate() → Finding[] (deduped)
  → separate_by_tiers() → {high, warning, needs_human_review}
  → redact_finding_evidence() → Finding[] (redacted)
  → generate_json_report() → JSON string
  → generate_md_report() → Markdown string
  → generate_sarif() → SARIF JSON string
  → ReviewDatabase.insert_*() → SQLite rows
```

## Extensibility

1. **Add a scanner**: Create function `scan_xxx(diff_file) → list[Finding]`, add to `_SCANNERS` dict
2. **Add a filter**: Add to `filter_policy.json` or create SafetyFilter in code
3. **Add a sandbox**: Implement `SandboxRunner` ABC, register in `create_sandbox_runner()`
4. **Add an output format**: Create `generate_xxx(report) → str`, call from `run_review.py`
5. **Schema migration**: Add entry to `COLUMN_MIGRATIONS` dict, increment `SCHEMA_VERSION`
