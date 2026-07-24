---
name: code-review
description: Automated code review rules and scripts. Parses unified diffs and detects security risks, async/resource leaks, DB lifecycle problems, missing tests, and secret leaks.
---
Overview

This skill reviews a unified diff. Stage the diff at work/inputs/changes.diff, then run
the scripts below from the workspace root. Every script prints a JSON object to stdout.

Scripts

- scripts/parse_diff.py <diff>       : prints {"summary": ..., "files": [...]}
- scripts/check_security.py <diff>   : security findings (eval/exec, shell=True, pickle, yaml.load, SQL injection)
- scripts/check_async_leak.py <diff> : async/resource leak findings (unreferenced tasks, unmanaged sessions/files)
- scripts/check_db_lifecycle.py <diff> : DB connection/cursor/transaction lifecycle findings
- scripts/check_tests_missing.py <diff> : source changed without test changes
- scripts/check_secrets.py <diff>    : hardcoded secrets (evidence pre-redacted)

Rules documentation lives under references/rules/.

Examples

1) python3 skills/code-review/scripts/parse_diff.py work/inputs/changes.diff
2) python3 skills/code-review/scripts/check_security.py work/inputs/changes.diff

Output contract

Checker scripts print: {"findings": [{"severity", "category", "file", "line",
"title", "evidence", "recommendation", "confidence", "source"}]}
