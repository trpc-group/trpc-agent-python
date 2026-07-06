# Skills Code Review Agent

This example implements the first phase of issue #92 as a deterministic,
local-only code review loop. It reads a unified diff, scans added lines with a
small static rule set, redacts likely secrets, and writes JSON and Markdown
reports.

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

## Not Implemented Yet

- Real LLM review.
- Real remote sandbox or container execution.
- SDK core integration.
- SQLite storage.
- Filter governance and telemetry.
- Multi-agent orchestration.
