---
name: code-review
description: Automated code review for Python projects. Analyzes git diffs to detect security issues, resource leaks, error handling problems, and missing tests.
---

# Code Review Skill

## Overview

This skill performs automated code review on Python code changes. It examines unified diffs,
identifies potential issues across 4 categories, and generates structured findings with severity
levels, evidence, and recommendations.

## Review Categories

1. **Security** — hardcoded secrets, unsafe deserialization, command injection (`shell=True`, `eval`, `exec`)
2. **Resource Leaks** — file handles without context managers, unclosed HTTP sessions, unclosed DB connections
3. **Error Handling** — swallowed exceptions, bare except clauses, missing error propagation
4. **Testing** — new functions/classes without corresponding test coverage

## Usage

Load this skill to analyze a git diff:

```
1. skill_load: code-review — load this skill
2. skill_run: parse_diff.py <diff_file> — parse the unified diff
3. skill_run: static_check.py — run deterministic checks
```

## Output Files

- `findings.json` — structured findings in JSON format
- `review_report.json` — final review report
- `review_report.md` — human-readable review report
