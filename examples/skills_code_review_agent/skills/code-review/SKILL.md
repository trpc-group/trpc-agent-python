---
name: code-review
description: Review normalized Git changes for security, async, resource, test, secret, and database defects.
---

# Code Review

Use this Skill only for structured code review. The caller stages a canonical
`review_input.json`; do not read unrelated workspace files.

## Required flow

1. Read `references/rules.md`.
2. Run the fixed scanner:

   ```text
   python scripts/scan_rules.py --input work/inputs/review_input.json --output out/findings.jsonl
   ```

3. Return `out/findings.jsonl` as the primary output.
4. Do not invoke a shell, network client, package manager, or another script.
5. Do not print environment variables or full input content.

Each JSONL object must contain exactly:

```text
severity, category, file, line, title, evidence,
recommendation, confidence, source
```

Only report changed lines or bounded hunk context. Evidence must be short and
must replace detected credential values with `[REDACTED]`.
