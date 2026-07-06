# Skills Code Review Agent

An automated code-review agent built on tRPC-Agent's Skills + sandbox + DB primitives (issue #92).
Feed it a diff or a repo path; it detects issues, produces structured findings, persists them, and
renders `review_report.json` + `review_report.md`.

> 中文说明见 [README.zh_CN.md](./README.zh_CN.md)。

## Quick start (no API key)

```bash
pip install -r requirements.txt

# Review a bundled fixture (no model needed). Default runtime is the sandbox
# (auto -> container if Docker is up, else the local subprocess sandbox):
python run_review.py --fixture security.diff --out-dir /tmp/cr

# Review your own diff, working tree, or an explicit file list:
python run_review.py --diff-file my.diff
python run_review.py --repo-path /path/to/repo --no-db
python run_review.py --files pipeline/engine.py,pipeline/scanners.py

# Scored self-test over the labelled fixtures (detection-rate / false-positive-rate):
python selftest.py

# Held-out danger/safe eval — independent evidence for the >=80% / <=15% thresholds on unseen code:
python selftest.py --holdout

# Run the review through the LlmAgent with the fake model (no API key needed):
python run_agent.py --fixture security.diff --dry-run
```

A sample report is committed under [`sample_output/`](./sample_output/); the rule catalog is in
[`../../skills/code-review/docs/RULES.md`](../../skills/code-review/docs/RULES.md) and the design note
in [DESIGN.md](./DESIGN.md).

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

Implemented: deterministic pipeline, DB persistence (incl. sandbox-run rows), 8 fixtures, scored
self-test, CLI, the fake-model agent loop (`run_agent.py`), and **sandbox execution**
(`--runtime local` runs the scanners in a subprocess sandbox with timeout + output cap and records
each run; `--runtime container` runs them in a Docker workspace — see `skills/code-review/Dockerfile`
— and is skipped in tests when Docker is absent). **Secret redaction** is hardened: `redact()` layers
provider-token regexes + a Shannon-entropy catch-all and hits 100% on the leak-test corpus with zero
false positives (criterion 5, ≥95%).

The **Filter gate** (criterion 7) is in place: `pipeline/policy.py::ReviewPolicy` decides
allow / deny / needs-human-review for a sandbox action (high-risk command, forbidden path,
non-whitelisted network, over-budget). It is enforced at two sites sharing that policy — the
deterministic sandbox gate (a denied action never launches; the block is recorded and surfaced in
the report's Filter-interception section) and the framework `agent/filter.py::ReviewGuardFilter`
(TOOL-scoped, attached on the review tool).

Rule coverage spans all six required categories (security, secret_leakage, async_errors,
resource_leak, db_lifecycle, missing_tests); the eight fixtures match the official scenarios
(`clean`, `security`, `async_resource_leak`, `db_lifecycle`, `missing_tests`, `duplicate_finding`,
`sandbox_failure`, `secret_redaction`). Inputs: `--diff-file`, `--repo-path`, `--files a.py,b.py`,
or `--fixture`. The default runtime is `auto` — the container sandbox when Docker is available, else
the local subprocess sandbox (`--runtime inprocess` is an explicit fast dev opt-in). The sandbox
receives only a whitelisted environment. See [DESIGN.md](./DESIGN.md) for the design note.

Remaining (non-code): an independent labelled eval set to prove the hidden-set thresholds, and
verifying the container runtime on a Docker host (the code path and `Dockerfile` are in place).
