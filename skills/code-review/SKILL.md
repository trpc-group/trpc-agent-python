---
name: code-review
description: Automated code review skill for analyzing diffs and detecting issues
os: ["linux", "macos", "windows"]
requires: ["python>=3.10"]
always: false
---

# Code Review Skill

Analyze code diffs to detect security vulnerabilities, code quality issues,
and best practice violations.

## Categories Covered

1. **Security** — Command injection, unsafe deserialization, dynamic imports
2. **Async Errors** — Event loop blocking, missing await, deprecated APIs
3. **Resource Leaks** — Unclosed file handles, connection leaks
4. **Database Lifecycle** — Unclosed cursors, missing commits, transaction issues
5. **Missing Tests** — New functions without corresponding test coverage
6. **Secret Information** — Hardcoded API keys, tokens, passwords

## Usage

Load this skill before running a code review. The skill provides:
- Rule definitions in `rules/`
- Execution scripts in `scripts/`
- Output schema reference in `docs/OUTPUT_SCHEMA.md`

## Output Format

Each finding includes:
- `severity`: critical, high, medium, low, info
- `category`: security, async_error, resource_leak, db_lifecycle, missing_tests, secret_info
- `file`, `line`: location
- `title`, `evidence`, `recommendation`
- `confidence`: 0.0 - 1.0
- `source`: scanner name

## Tools

- `scripts/run_checks.py` — Run all enabled scanners against a diff file
- `scripts/parse_diff.py` — Parse unified diff into structured format
