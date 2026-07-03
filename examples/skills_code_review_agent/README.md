# Skills Code Review Agent

An automated code-review agent built on tRPC-Agent's Skills + sandbox + DB primitives (issue #92).
Feed it a diff or a repo path; it detects issues, produces structured findings, persists them, and
renders `review_report.json` + `review_report.md`.

> 中文说明见 [README.zh_CN.md](./README.zh_CN.md)。

## Quick start (no API key)

```bash
pip install -r requirements.txt

# Review a bundled fixture (dry-run, deterministic — no model needed):
python run_review.py --fixture 0001_insecure.diff --out-dir /tmp/cr

# Review your own diff or working tree:
python run_review.py --diff-file my.diff
python run_review.py --repo-path /path/to/repo --no-db

# Scored self-test over the labelled fixtures (detection-rate / false-positive-rate):
python selftest.py
```

## How it works

Findings come from **deterministic static scanners**, not the LLM, so results are reproducible and
the acceptance thresholds are tunable:

```
diff/repo ──▶ diff_parser ──▶ scanners ──▶ dedup/denoise ──▶ redact ──▶ report (json+md)
              (unidiff)      (bandit,       (per file/line/   (single      │
                             ruff,           category;         choke-       ▼
                             detect-secrets, confidence →      point)     ReviewStore
                             semgrep)        active/warning/              (SqlStorage:
                                             human-review)                 SQLite default,
                                                                           PG/MySQL by URL)
```

| Category | Scanner |
|---|---|
| security | bandit, semgrep |
| secret_leakage | detect-secrets |
| async_errors | ruff (ASYNC) |
| resource_leak | ruff (SIM115 / bugbear) |
| db_lifecycle | semgrep (`skills/code-review/rules/db_lifecycle.yaml`) |

## Design note

The backbone is a **deterministic pipeline**; the agent (Skills + sandbox + Filter) is *one of two
finding sources*, not the orchestrator. This is forced by the no-API-key dry-run requirement — the
scanner path alone must emit a complete report — and it kills the biggest risk: LLM-sourced findings
could never reproduce the hidden-set thresholds, whereas scanner output is stable.

**Skill design.** `skills/code-review/` packages the review as a portable Skill (`SKILL.md` +
`scripts/run_checks.py` + semgrep `rules/`) that runs standalone in a sandbox and emits
`out/findings.json` per `docs/OUTPUT_SCHEMA.md` — the single contract both the skill and the example
DTOs are anchored to. **Sandbox strategy.** Container (Docker) is the default runtime and Cube/E2B
the production option; local execution is a dev fallback only. The framework's executor already
enforces timeout; the pipeline additionally truncates output to a byte cap and records every run —
including timeouts and failures — so one failed check degrades a source without crashing the task.
**Filter strategy.** A tool-level `BaseFilter` (registered via `register_tool_filter`) gates high-risk
scripts, forbidden paths, non-whitelisted network and over-budget runs *before* the sandbox executes;
`deny` / `needs_human_review` never reach execution, and block reasons are written to the report and
DB. **Monitoring.** Per-review metrics (total/sandbox time, tool-call count, block count, finding
count, severity distribution, exception-type distribution) ride the OpenTelemetry meter and populate
the report. **DB schema.** Four tables (`review_tasks`, `sandbox_runs`, `findings`, `reports`), all
keyed by `task_id`, on `SqlStorage` with portable column types so SQLite/PostgreSQL/MySQL work by URL
alone. **Dedup & denoise.** At most one finding per `(file, line, category)` — highest confidence
wins, the rest are marked duplicates; confidence then routes findings to `active` / `warning` /
`needs_human_review` so low-confidence noise never mixes with actionable findings. **Security
boundary.** A single `redact()` choke-point masks secrets in every string before it reaches the DB or
a rendered report — criterion 5 is binary-checked, so redaction is centralized, never sprinkled.

## Status

Implemented: deterministic pipeline, DB persistence, 8 fixtures, scored self-test, CLI. Baseline
secret redaction is in place. Planned follow-up slices: in-sandbox execution (Container/Cube), the
tool-level Filter gate, redaction hardening to ≥95%, OpenTelemetry metrics wiring, and the
fake-model agent loop that drives the review as a Skill tool.
