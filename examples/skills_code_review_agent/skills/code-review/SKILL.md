---
name: code-review
description: Automated code review rules and sandboxed check scripts covering security risks, hardcoded secrets, async mistakes, resource leaks, database lifecycle problems and missing tests.
---

Overview

Run deterministic code-review checks over a changed-file set (parsed from a
unified diff) inside an isolated workspace. The skill ships six rule
categories, each implemented as an AST-first Python checker with an explicit
false-positive policy, plus a diff parser so the skill also works standalone.

Rule documentation lives in docs/ (one file per category):

- docs/rules-security.md — injection, eval/exec, unsafe deserialization
- docs/rules-secrets.md — hardcoded credentials, allowlist policy
- docs/rules-async.md — blocking calls in async code, missing await
- docs/rules-resource-leak.md — unclosed handles, ownership-transfer rules
- docs/rules-db-lifecycle.md — connections, cursors, transactions
- docs/rules-missing-tests.md — test-coverage heuristics, noise policy

Inputs

The host stages a pre-parsed `review_input.json` under `work/inputs/`
(schema: mode, files[] with post-image content and candidate line numbers).
Without it, any `*.diff` / `*.patch` under `work/inputs/` is parsed on the
fly by scripts/parse_diff.py.

Examples

1) Run every check once and collect a single findings JSON

   Command:

   python3 scripts/run_checks.py

   Findings are written to $OUTPUT_DIR/findings.json (collected
   automatically by skill_run; stdout stays small on purpose).

2) Parse a raw unified diff into structured JSON (standalone use)

   Command:

   python3 scripts/parse_diff.py work/inputs/change.diff out/parsed.json

Output Files

- out/findings.json — {"version", "engine", "stats", "findings": [{rule_id,
  category, severity, precision, file, line, title, evidence,
  recommendation, fix_snippet, confidence, source}]}
- out/parsed.json — structured diff (files, hunks, per-side line numbers)

Notes

- Checks use only the Python standard library; no network, no external
  packages. A crashing check is isolated and recorded in stats.errors.
- Secret values are already masked to a 4-character prefix inside evidence
  before they leave the sandbox.
