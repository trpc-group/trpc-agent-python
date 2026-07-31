# Skills Code Review Agent

An automated code-review agent built on tRPC-Agent's Skills + sandbox + DB primitives (issue #92).
Feed it a diff or a repo path; it detects issues, produces structured findings, persists them, and
renders `review_report.json` + `review_report.md`.

> 中文说明见 [README.zh_CN.md](./README.zh_CN.md)。

## Quick start

```bash
pip install -r requirements.txt
docker build -t cr-scanners:latest ../../skills/code-review   # the sandbox image

# The product path: an LlmAgent loads the code-review Skill and runs it in a sandbox.
export TRPC_AGENT_API_KEY=...            # any OpenAI-compatible endpoint; see .env.example
python run_agent.py --fixture security.diff

# Same four steps with no API key and no Docker (issue criterion 6):
python run_agent.py --fixture security.diff --dry-run

# The deterministic CLI: same skill script, launched directly. Used for scoring the fixtures.
python run_review.py --fixture security.diff --out-dir /tmp/cr

# Review your own diff, working tree, or an explicit file list:
python run_review.py --diff-file my.diff
python run_review.py --repo-path /path/to/repo --no-db
python run_review.py --files pipeline/engine.py,pipeline/devrun.py

# Scored self-test over the labelled fixtures (detection-rate / false-positive-rate):
python selftest.py

# Held-out danger/safe eval — independent evidence for the >=80% / <=15% thresholds on unseen code:
python selftest.py --holdout
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

The agent is the orchestrator, and the Skill is the single source of findings. A review is four
steps: `stage_review_input` materializes the diff on the host, `skill_load` loads `SKILL.md` and its
rule docs through the framework's Skills mechanism, `skill_run` executes the skill's own
`scripts/run_checks.py` inside a workspace sandbox, and `finalize_review` dedups, persists and
renders. The model decides and reports; it never invents a finding, because every finding comes from
a scanner that ran in the sandbox.

Findings are deterministic *because the scanners are*, not because the agent is bypassed — which is
what lets the same path satisfy both the product requirement and the no-API-key dry-run (criterion
6): `--dry-run` swaps the model, not the pipeline, so it walks the identical four steps.

**Skill design.** `skills/code-review/` packages the review as a portable Skill (`SKILL.md` +
`scripts/run_checks.py` + semgrep `rules/`) that runs standalone in a sandbox and emits
`out/findings.json` per `docs/OUTPUT_SCHEMA.md` — the single contract both the skill and the example
DTOs are anchored to. **Sandbox strategy.** Container (Docker) is the default runtime; local execution is a development
fallback only, never implicitly selected. The framework's `skill_run` enforces the timeout and
collects the output; every run — including timeouts and blocks — is recorded as a `SandboxRunResult`,
so one failed check degrades a source without crashing the task. Because the workspace runtimes stage
inputs at different depths, the skill script locates its own scan root from the `.changes.diff`
sidecar staged beside the changed files; findings' paths therefore match the diff under any layout.
**Filter strategy.** A tool-level `BaseFilter` gates high-risk scripts, forbidden paths,
non-whitelisted network and over-budget runs *before* the sandbox executes; `deny` /
`needs_human_review` never reach execution, and block reasons are written to the report and DB. The
filter is attached to `skill_run`, and the toolset is narrowed to `skill_load` + `skill_run` — the
stock `SkillToolSet` also exposes `skill_exec` / `workspace_exec`, which are built without filters
and would otherwise let the model route around the gate entirely. **Monitoring.** Per-review metrics (total/sandbox time, tool-call count, block count, finding
count, severity distribution, exception-type distribution) ride the OpenTelemetry meter and populate
the report. **DB schema.** Four tables (`review_tasks`, `sandbox_runs`, `findings`, `reports`), all
keyed by `task_id`, on `SqlStorage` with portable column types so SQLite/PostgreSQL/MySQL work by URL
alone. **Dedup & denoise.** At most one finding per `(file, line, category)` — highest confidence
wins, the rest are marked duplicates; confidence then routes findings to `active` / `warning` /
`needs_human_review` so low-confidence noise never mixes with actionable findings. **Security
boundary.** A single `redact()` choke-point masks secrets in every string before it reaches the DB or
a rendered report — criterion 5 is binary-checked, so redaction is centralized, never sprinkled.

## Status

The agent path is the product path: a real model by default, the Skill loaded through
`SkillToolSet` -> `skill_load` -> `skill_run`, the scanners in a container sandbox, results
deduplicated, persisted and rendered. `--dry-run` substitutes `FakeReviewModel`, which drives the
same four steps with no API key and no Docker (criterion 6).

`skills/code-review/scripts/run_checks.py` is the only implementation of the review rules. The agent
reaches it through the Skills mechanism; the deterministic CLI (`run_review.py`, `selftest.py`)
reaches the same script through a development subprocess. Nothing re-implements the rules elsewhere.

**Filter gate** (criterion 7/8): `pipeline/policy.py::ReviewPolicy` decides allow / deny /
needs-human-review, enforced by `agent/filter.py::ReviewGuardFilter` on `skill_run` before the
sandbox runs, and by the same policy in the development runner. Blocks are recorded and surfaced in
the report's Filter-interception section. The sandbox receives only a whitelisted environment.

**Secret redaction** (criterion 5): `redact()` layers provider-token regexes plus a Shannon-entropy
catch-all and hits 100% on the leak-test corpus with zero false positives; the skill script redacts
at emit time as well, so nothing unredacted reaches the model's context.

Rule coverage spans all six required categories (security, secret_leakage, async_errors,
resource_leak, db_lifecycle, missing_tests); the eight fixtures match the official scenarios
(`clean`, `security`, `async_resource_leak`, `db_lifecycle`, `missing_tests`, `duplicate_finding`,
`sandbox_failure`, `secret_redaction`). Inputs: `--diff-file`, `--repo-path`, `--files a.py,b.py`,
or `--fixture`. See [DESIGN.md](./DESIGN.md) for the design note.

**Tests.** `pytest tests/examples/test_skills_code_review_agent.py` — the suite asserts the four-step
skill sequence, that `SKILL.md`'s content reaches the model, and that findings' file paths match the
diff. Emptying `SKILL.md` turns two tests red; deleting it turns three red. A real-model integration
test runs under `CR_LIVE_MODEL_TEST=1` with an API key and skips cleanly without one.

Remaining: an independent labelled eval set to prove the hidden-set thresholds.
