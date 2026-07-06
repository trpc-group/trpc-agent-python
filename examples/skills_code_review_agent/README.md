# Skills Code Review Agent

This example implements the early phases of issue #92 as a deterministic,
local-only code review loop. It reads a unified diff, scans added lines with a
small static rule set, redacts likely secrets, and writes JSON and Markdown
reports. When `--db-path` is provided, it also persists the review task,
findings, report metadata, sandbox run metadata, and filter decisions into
SQLite.

This is a lightweight deterministic baseline, not a full sandbox/scanner
implementation. It intentionally does not call an LLM, remote sandbox, Docker,
Cube, E2B, external scanner, or SDK core extension. The sandbox runner in this
example is a fake dry-run runner: it records what would have happened, but
never executes untrusted code.

## Run

From the repository root:

```bash
python examples/skills_code_review_agent/run_agent.py --diff-file examples/skills_code_review_agent/fixtures/security.diff --output-dir examples/skills_code_review_agent/output --dry-run
```

Expected output files:

```text
examples/skills_code_review_agent/output/
  review_report.json
  review_report.md
```

You can also run the clean fixture:

```bash
python examples/skills_code_review_agent/run_agent.py --diff-file examples/skills_code_review_agent/fixtures/clean.diff --output-dir examples/skills_code_review_agent/output_clean --dry-run
```

## Run From Stdin

`--diff-file -` reads a unified diff from stdin and records the report input as
`<stdin>`:

```bash
git diff | python examples/skills_code_review_agent/run_agent.py --diff-file - --output-dir examples/skills_code_review_agent/output --dry-run
```

## CI Failure Gate

By default the command exits with `0` even when findings are present. Use
`--fail-on-severity` to make a lightweight CI gate:

```bash
python examples/skills_code_review_agent/run_agent.py --diff-file examples/skills_code_review_agent/fixtures/security.diff --output-dir examples/skills_code_review_agent/output --dry-run --fail-on-severity high
```

Values are `never`, `low`, `medium`, and `high`. For example, `medium` fails on
medium or high findings, while `high` fails only on high findings.

## List Rules

```bash
python examples/skills_code_review_agent/run_agent.py --list-rules
```

This prints the deterministic rule id, category, default severity, description,
and known limitations without reading a diff.

## Run With SQLite

```bash
python examples/skills_code_review_agent/run_agent.py --diff-file examples/skills_code_review_agent/fixtures/security.diff --output-dir examples/skills_code_review_agent/output --db-path examples/skills_code_review_agent/output/reviews.sqlite3 --dry-run
```

When `--db-path` is set, the command prints the database path and generated
task id. The database contains these tables:

- `review_tasks`
- `findings`
- `reports`
- `sandbox_runs`
- `filter_decisions`

## Verify SQLite Records

Using Python:

```bash
python -c "import sqlite3; db='examples/skills_code_review_agent/output/reviews.sqlite3'; con=sqlite3.connect(db); print(con.execute('select task_id,total_findings from review_tasks order by created_at desc limit 1').fetchone()); print(con.execute('select severity,category,file,line,title from findings order by id limit 5').fetchall())"
```

Using the `sqlite3` CLI:

```bash
sqlite3 examples/skills_code_review_agent/output/reviews.sqlite3 "select severity, category, file, line, title from findings order by id;"
```

## Run Tests

```bash
python -m pytest examples/skills_code_review_agent/tests
```

The tests run only local parsing, rules, redaction, report generation, and
dedupe checks, fake sandbox status, filter decisions, telemetry summaries, and
SQLite persistence. They also cover the lightweight CI failure gate, stdin diff
input, and rule listing. They do not call an LLM, Docker, Cube, remote network,
external scanners, or any external service.

## Fixtures

- `clean.diff`: safe changes; should produce no high-severity findings.
- `security.diff`: mixed security sample covering secret, missing timeout, broad exception, SQL risk, and resource lifecycle.
- `sql_injection.diff`: SQL string concatenation.
- `missing_timeout.diff`: `httpx` request without `timeout=`.
- `broad_except.diff`: broad `except Exception` with swallowed failure.
- `resource_leak.diff`: `open(...)` without a context manager.
- `duplicate.diff`: duplicate hunk producing the same finding, used to verify dedupe.
- `secret_redaction.diff`: multiple secret-like values, used to verify reports omit plaintext secrets.

## Current Scope

- Parses unified diff hunks and added line numbers.
- Runs five deterministic rules:
  - hardcoded secret / token / password
  - SQL string concatenation risk
  - `requests` / `httpx` calls missing `timeout=`
  - broad `except Exception` or simple error swallowing
  - `open(...)` without `with`
- Redacts likely API keys, tokens, secrets, and passwords before writing reports.
- Produces `review_report.json` and `review_report.md`.
- Supports reading a unified diff from stdin with `--diff-file -`.
- Supports a lightweight exit-code gate with `--fail-on-severity`.
- Prints rule metadata with `--list-rules`.
- Optionally persists review tasks, findings, and report metadata to SQLite.
- Records minimal filter decisions: `allow`, `deny`, or `needs_human_review`.
- Records a fake dry-run sandbox result without executing untrusted code.
- Adds telemetry summary fields to JSON and Markdown reports.
- Includes local pytest coverage for the deterministic rule fixtures.

## Sandbox Notes

The current `dry-run` sandbox runner is deliberately fake and safe for local
development. It simulates a static-check pass and records status/timing fields.
It does not run shell commands, install dependencies, invoke Docker, or reach a
remote sandbox service.

A true local runner should only be used as a development fallback because it
does not isolate untrusted code. Later phases can replace this example runner
with the project-level `ContainerWorkspaceRuntime` or `CubeWorkspaceRuntime`
without changing the report and storage shape introduced here.

## Not Implemented Yet

- Real LLM review.
- Real remote sandbox or container execution.
- External scanner integration such as Bandit, Ruff, detect-secrets, or Semgrep.
- SDK core integration.
- Production-grade filter governance and telemetry export.
- Multi-agent orchestration.
