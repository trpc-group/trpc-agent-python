# Skills Code Review Agent

This example implements the early phases of issue #92 as a deterministic,
local-only code review loop. It reads a unified diff, scans added lines with a
small static rule set, redacts likely secrets, and writes JSON and Markdown
reports. When `--db-path` is provided, it also persists the review task,
findings, and report metadata into SQLite.

It intentionally does not call an LLM, remote sandbox, or SDK core extension.

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

## Run With SQLite

```bash
python examples/skills_code_review_agent/run_agent.py --diff-file examples/skills_code_review_agent/fixtures/security.diff --output-dir examples/skills_code_review_agent/output --db-path examples/skills_code_review_agent/output/reviews.sqlite3 --dry-run
```

When `--db-path` is set, the command prints the database path and generated
task id. The database contains three tables:

- `review_tasks`
- `findings`
- `reports`

## Verify SQLite Records

Using Python:

```bash
python -c "import sqlite3; db='examples/skills_code_review_agent/output/reviews.sqlite3'; con=sqlite3.connect(db); print(con.execute('select task_id,total_findings from review_tasks order by created_at desc limit 1').fetchone()); print(con.execute('select severity,category,file,line,title from findings order by id limit 5').fetchall())"
```

Using the `sqlite3` CLI:

```bash
sqlite3 examples/skills_code_review_agent/output/reviews.sqlite3 "select severity, category, file, line, title from findings order by id;"
```

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
- Optionally persists review tasks, findings, and report metadata to SQLite.

## Not Implemented Yet

- Real LLM review.
- Real remote sandbox or container execution.
- SDK core integration.
- Filter governance and telemetry.
- Multi-agent orchestration.
