# Skills Code Review Agent — Design

## 1. Overview

The Skills Code Review Agent is an automated code-review prototype built on the tRPC-Agent-Python SDK. It takes a git diff, PR patch, or local working-tree changes, loads review rules and scripts through a `code-review` Skill, executes checker scripts inside an isolated workspace (after Filter governance approval), produces structured findings, and persists tasks, sandbox runs, filter intercepts, monitoring summaries, and reports to a SQL database (SQLite by default).

## 2. Architecture

```
   Unified diff (file / repo / fixture)
              │
              ▼
      +-- Parse diff --+
      |  (parse_diff)  |
      +----------------+
              │
              ▼
   +--- Governance Filter ----+
   | script allowlist, paths, |
   | network deny, budget,    |
   | risk → needs_human_review|
   +--------------------------+
              │
              ▼
   +--- Sandbox (container / local / cube) ---+
   | env -i (PATH+HOME+LANG whitelist)        |
   | security | async_leak | db_lifecycle     |
   | tests_missing | secrets                   |
   | timeout: 60s, output cap: 256KB           |
   +-------------------------------------------+
              │
              ▼
   +--- LLM enrichment (optional) ---+
   | confidence enrichment,          |
   | false-positive suppression      |
   +---------------------------------+
              │
              ▼
   +--- Dedup + Gating ---+
   | file:line:category    |
   | confidence >= 0.6     |
   +-----------------------+
              │
              ▼
   +--- Persist to SQLite ---+
   | 6 tables, task-id       |
   +-------------------------+
              │
              ▼
   +-- Redaction + Reports --+
   | review_report.json/.md  |
   +-------------------------+
```

The pipeline is deterministic: parse → govern → sandbox → merge → dedup → persist → report. Rule scripts inside the `code-review` skill produce baseline findings (`source: "static"`) — they are deterministic, so the accuracy requirements (≥80% detection, ≤15% false positives) do not depend on an LLM. An optional `LlmAgent` enrichment step can add or confirm findings. In `--dry-run` mode a `FakeReviewModel(LLMModel)` returns deterministic responses so the entire chain runs without any API key.

## 3. Skill Design

The `code-review` Skill follows the tRPC-Agent skill layout:

```
skills/code-review/
├── SKILL.md                    # frontmatter + usage + rules index
├── references/rules/           # one rule doc per category
└── scripts/
    ├── diffparse.py            # unified-diff parser (stdlib)
    ├── parse_diff.py           # CLI: diff → JSON summary
    ├── check_security.py       # eval/exec, shell=True, pickle, yaml.load, SQL injection
    ├── check_async_leak.py     # unreferenced tasks, unmanaged sessions/files
    ├── check_db_lifecycle.py   # connection/cursor/transaction lifecycle
    ├── check_tests_missing.py  # source changes without test changes
    ├── check_secrets.py        # hardcoded secrets (evidence pre-redacted)
    ├── secret_patterns.py      # shared pattern library (sandbox + host)
    └── checklib.py             # shared helpers: load_files, finding, emit
```

Every checker script prints a JSON contract to stdout: `{"findings": [...]}` where each finding has `severity, category, file, line, title, evidence, recommendation, confidence, source`. This contract is the script↔host interface.

## 4. Sandbox Isolation

- **Container (production default)**: uses `create_container_workspace_runtime()` with the `python:3-slim` Docker image. The code-review skill directory is staged into the workspace via `stage_directory`. The diff file is staged at `work/inputs/changes.diff`.
- **Local (dev fallback)**: `create_local_workspace_runtime()` — prints a warning; not for production.
- **Cube/E2B**: configured from environment credentials.

### Safety Boundaries

| Boundary | Mechanism |
|---|---|
| Environment whitelist | `env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp LANG=C.UTF-8` — only three environment variables reach the sandbox process. Host environment is completely stripped. |
| Timeout | 60 seconds per script (configurable). Total task budget: 300 seconds / 20 runs. |
| Output cap | 256 KB per stdout/stderr stream. Truncated outputs carry a marker. |
| Failure resilience | Non-zero exit, timeout, or runtime error are recorded as failed `sandbox_runs` rows with `error_type`. The review continues with remaining scripts; sandbox failures never crash the task. |

## 5. Filter Governance

The `GovernanceEngine` enforces a layered policy:

| Policy | Rule | Decision |
|---|---|---|
| Script allowlist | Only 6 known checker scripts may execute | `deny` |
| Forbidden paths | Absolute paths, `..`, `~` escapes | `deny` |
| Network deny | `curl`, `wget`, `pip`, `git`, `ssh`, `apt` etc. | `deny` |
| Risk escalation | `sudo`, `docker`, `chmod`, `rm`, `mkfs` etc. | `needs_human_review` |
| Budget | Exceeding 20 runs or 300s cumulative sandbox time | `deny` |

Dual enforcement:
1. The deterministic orchestrator in `pipeline.py` consults the engine before every sandbox run.
2. `GovernanceToolFilter` (a `BaseFilter` of type `TOOL`) guards LLM-initiated `skill_run` tool calls via `check_command()`.

`deny` and `needs_human_review` decisions never reach the sandbox. Every decision is written to `cr_filter_events` and summarized in the report.

## 6. Finding Processing

- **Schema**: `Finding` pydantic model — `severity, category, file, line, title, evidence, recommendation, confidence, source`.
- **Dedup**: key = `(file, line, category)`. Duplicates merge: highest severity, highest confidence, union of sources (becomes `"static+llm"`). Dropped rows recorded with status `deduped`.
- **Confidence gating**: findings with `confidence < 0.6` are excluded from main findings and placed in the report's `needs_human_review` section.

## 7. Database Schema

Six tables on a dedicated SQLAlchemy `DeclarativeBase`, managed by the SDK's `SqlStorage(is_async=False, db_url=..., metadata=CrBase.metadata)`. Default: `sqlite:///code_review.db`. Any SQLAlchemy `db_url` works.

| Table | Key columns |
|---|---|
| `cr_review_tasks` | `id` (uuid), `created_at`, `finished_at`, `status`, `input_type`, `input_ref`, `runtime`, `dry_run`, `diff_summary` (JSON) |
| `cr_sandbox_runs` | `id`, `task_id` (FK), `script`, `category`, `status`, `exit_code`, `duration_ms`, `timed_out`, `stdout_summary` (redacted), `stderr_summary`, `error_type` |
| `cr_findings` | `id`, `task_id` (FK), `severity`, `category`, `file`, `line`, `title`, `evidence` (redacted), `recommendation`, `confidence`, `source`, `status`, `dedup_key` |
| `cr_filter_events` | `id`, `task_id` (FK), `target`, `decision`, `rule`, `reason` |
| `cr_metrics` | `id`, `task_id` (FK), `total_duration_ms`, `sandbox_duration_ms`, `tool_calls`, `intercepts`, `findings_total`, `severity_distribution` (JSON), `error_distribution` (JSON) |
| `cr_reports` | `id`, `task_id` (FK), `report_json` (JSON), `report_md` |

`ReviewStore.get_task_bundle(task_id)` returns all six collections joined by task id.

## 8. Monitoring

Every review records into `cr_metrics`:

- `total_duration_ms`: wall-clock duration of the entire review
- `sandbox_duration_ms`: cumulative sandbox execution time across all scripts
- `tool_calls`: total sandbox runs (+ LLM call if enabled)
- `intercepts`: count of non-`allow` filter decisions
- `findings_total`: count of reported (non-deduped, high-confidence) findings
- `severity_distribution`: JSON dict of `{"critical": N, "high": N, ...}`
- `error_distribution`: JSON dict of `{"timeout": N, "some_exception": N, ...}`

OTel spans from the SDK (`invocation`, `agent_run`, `execute_tool`) remain active; the custom metrics table provides queryable aggregates without an OTel backend.

## 9. Redaction

A shared `secret_patterns.py` module (stdlib-only) serves as the single source of truth. Both `check_secrets.py` (sandbox side) and `review/redaction.py` (host side) import and use it.

Pattern categories: OpenAI/Anthropic/AWS/GitHub/Slack API keys, Bearer tokens, JWTs, PEM private keys, URL basic-auth, sensitive variable assignments (`password`, `secret`, `token`, `api_key`, etc.).

Replacement format: `***REDACTED-<sha256:8>***` — stable fingerprint per secret value for traceability.

Applied at every persistence boundary:
1. Checker script evidence (pre-redacted inside the sandbox)
2. Sandbox stdout/stderr summaries (in `ReviewStore.add_sandbox_run`)
3. Finding evidence (in `ReviewStore.add_findings`)
4. Filter event targets (in `ReviewStore.add_filter_event`)
5. Report JSON and Markdown (in `write_reports` and `ReviewStore.add_report`)
6. Report dict returned to callers (in `run_review`)

Target: ≥95% detection rate; no plaintext secrets in any report file or database row.

## 10. Fake Model / Dry-Run

`FakeReviewModel(LLMModel)` provides a deterministic response: `{"summary": "Dry-run review complete. Static findings are authoritative.", "findings": []}`. The `supported_models()` classmethod returns `[r"fake-review-.*"]`.

In dry-run mode the entire pipeline (parse, govern, sandbox, dedup, persist, report) runs identically; only the model differs. Target: ≤2 minutes (actual: ~5 seconds local, CI-measured). Dry-run auto-activates when `TRPC_AGENT_API_KEY` is unset.

## 11. Report Output

Both `review_report.json` and `review_report.md` contain:

1. Findings summary with severity distribution
2. `needs_human_review` items (low-confidence + filter escalations)
3. Filter intercept summary (decision, rule, reason per event)
4. Monitoring metrics (durations, counts, distributions)
5. Sandbox execution summary (per script: status, duration, failure info)
6. Actionable fix recommendations (per finding)
7. Final conclusion: `pass` / `needs_attention` / `blocked`

## 12. Directory Layout

```
examples/skills_code_review_agent/
├── README.md
├── run_agent.py              # CLI (review / show subcommands)
├── doc/
│   ├── design.md             # this document (English)
│   └── design_zh.md          # Chinese version
├── agent/                    # LlmAgent wiring + fake model
├── skills/code-review/       # SKILL.md + scripts + rule docs
├── review/                   # pipeline, findings, sandbox, governance, redaction, report
├── storage/                  # SQLAlchemy models + ReviewStore
├── filters_cr/               # GovernanceToolFilter (BaseFilter)
├── fixtures/                 # 8 diff samples
├── tests/                    # 65 pytest tests
└── sample_output/            # committed example output
```

## 13. Out of Scope

- Real CI/PR platform integration (GitHub webhooks, etc.)
- Semantic/embedding-based memory of past reviews
- Multi-language checkers — rules target Python sources
- Hidden-sample tuning beyond the documented rule patterns
