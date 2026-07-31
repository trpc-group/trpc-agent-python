---
name: code-review
description: Code review skill to parse diffs, run sanity checks, and review rules on modified files.
---

# Code Review Skill

This skill provides utilities for parsing git diff/PR patches and executing linting, static checks, or testing rules in a sandboxed workspace environment.

## Usage

1. Parse git unified diff:
   `python3 scripts/parse_diff.py --diff work/inputs/input.diff --output work/inputs/parsed_diff.json`

2. Run static analysis rules (covering security, async, db connection, resource leak, and sensitive credentials):
   `python3 scripts/run_checks.py --parsed-diff work/inputs/parsed_diff.json --src-dir work/inputs/repo/ --output out/raw_findings.json`
