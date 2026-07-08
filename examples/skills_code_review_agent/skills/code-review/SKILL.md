---
name: code-review
description: Structured code review skill with diff parsing, static checks, sandbox execution policy, redaction and report generation.
---

# Code Review Skill

Use this skill to review unified diffs, PR patches or local git changes. The skill is designed for a sandboxed workspace:

1. Load the diff into `work/inputs/input.diff`.
2. Run `scripts/parse_diff.py work/inputs/input.diff out/diff_summary.json`.
3. Run `scripts/static_rules.py work/inputs/input.diff out/static_findings.json`.
4. Merge deterministic rule findings with model review findings only after redaction and deduplication.
5. Persist task, sandbox runs, filter intercepts, metrics, findings and final report.

Tools:
- skill_run

## Review Contract

Every finding must include:

- `severity`: critical, high, medium, low or info.
- `category`: security, async_error, async_resource, resource_leak, testing, sensitive_info, db_lifecycle or sandbox.
- `file` and `line`: changed file path and candidate new-line number.
- `title`, `evidence`, `recommendation`, `confidence`, `source`.

Low-confidence items must be emitted as warnings or `needs_human_review`, not as high-confidence findings.

## Safety Rules

Do not run network, package installation, destructive filesystem, privilege escalation, SSH, Docker or curl-pipe-shell commands without an explicit Filter allow decision. Denied or `needs_human_review` commands must be recorded in the report and database instead of being executed.

Only pass whitelisted environment variables into the sandbox. Redact API keys, tokens, passwords, private keys and bearer credentials before writing logs, reports or database rows.

