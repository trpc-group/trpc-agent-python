# Skills code-review agent

This prototype composes a reusable `code-review` Skill, deterministic diff rules, a Filter-governed container runner,
SQLite persistence, redaction, monitoring, and JSON/Markdown reports. It accepts unified diffs, a Git workspace, or a
file list.

Run the complete flow without an API key or Docker:

```bash
python examples/skills_code_review_agent/cli.py \
  --diff-file examples/skills_code_review_agent/fixtures/security.diff \
  --dry-run
```

Outputs are `review_report.json`, `review_report.md`, and `reviews.db`. Query a task with
`SQLiteReviewStore("reviews.db").get_task(task_id)`. The store interface separates orchestration from SQLite so another
SQL backend can implement the same three methods.

Production policy defaults to `container`, never local execution. The runner uses Docker with no network, a read-only
workspace, memory/CPU/PID limits, a timeout, capped output, and an environment allowlist. `--dry-run` selects the fake
runner for tests. Local mode exists only as an explicit development fallback. Every command is checked before sandbox
entry; `deny` and `needs_human_review` are persisted and never executed.

The static rules cover security, async errors, file/network resource lifetime, database connection lifetime, secrets,
and missing tests. They only inspect added lines and are intentionally conservative. Confidence below 0.75 is routed to
human-review warnings. Findings are deduplicated by file, line, and category. Static analysis and containers reduce risk
but do not prove correctness or replace least-privilege infrastructure.

See `DESIGN.md`, `schema.sql`, and `skills/code-review/references/rules.md` for the architecture and extension points.
