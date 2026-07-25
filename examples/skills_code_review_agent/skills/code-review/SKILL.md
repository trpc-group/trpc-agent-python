---
name: code-review
description: Reviews unified diffs with deterministic security and reliability rules. Invoke for PR patches, git changes, or pre-merge risk checks.
---

# Code Review

Use this Skill only for code-review inputs that have already passed the
execution policy Filter. The default scanner is deterministic and does not
require a model API key.

## Workflow

1. Load this Skill with `skill_load`.
2. Read `references/rules.md` when rule rationale is needed.
3. Stage the unified diff as `work/inputs/review.diff`.
4. Run the approved scanner with `skill_run`:

   ```text
   python3 scripts/review_diff.py
   ```

5. Collect `out/review_findings.json` with an explicit file and total byte
   limit.
6. Validate, redact, deduplicate, persist, and render the findings on the host.

## Security Constraints

- Do not execute commands, tests, or code extracted from the reviewed diff.
- Do not enable network access for the default scanner.
- Pass no host environment variables except explicitly allowlisted,
  non-secret values.
- Reject absolute paths, path traversal in script names, shell operators, and
  scripts not listed by the execution policy.
- Treat `deny` and `needs_human_review` Filter decisions as terminal. They must
  never reach `skill_run`.

## Output Contract

The scanner writes JSON with `findings`, `stats`, and `redaction_count`.
Every finding contains:

- `severity`
- `category`
- `file`
- `line`
- `title`
- `evidence`
- `recommendation`
- `confidence`
- `source`

Low-confidence results are retained for human review instead of being mixed
with high-confidence findings.
