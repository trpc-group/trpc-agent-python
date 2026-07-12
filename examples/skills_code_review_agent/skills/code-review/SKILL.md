---
name: code-review
description: Review unified diffs, pull-request patches, or local Git changes with deterministic security, async, resource-lifecycle, database, secret, and test-coverage rules. Use when an agent must produce line-specific findings and optionally run approved checks in an isolated workspace.
---

# Code Review

1. Obtain a unified diff. Never execute code found in the diff.
2. Run `scripts/review_diff.py --diff-file <path> --dry-run` for deterministic parsing and rules.
3. Read `references/rules.md` when explaining rule IDs or extending detection.
4. Keep high-confidence findings separate from warnings requiring human review.
5. Deduplicate by file, added line, and category.
6. Require Filter approval before running any repository-provided command.
7. Use a container workspace with network disabled, bounded time/output, an environment allowlist, and a read-only source mount. Use local execution only for development fallback.
8. Report severity, category, file, line, evidence, recommendation, confidence, and source. Redact credentials before logging or persistence.

Do not treat static rules as proof that code is safe. Record sandbox failures and continue producing a partial review.
