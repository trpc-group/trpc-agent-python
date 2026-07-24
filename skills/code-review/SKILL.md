---
name: code-review
description: Review a code change for security, resource, async, secret-leakage and DB-lifecycle issues by running established static scanners in a sandbox and emitting structured findings.
---

# Code Review skill

Reviews a set of changed files by running mature static-analysis scanners and normalizing their
output into a single findings contract. Designed to run inside a sandboxed workspace (Container or
Cube/E2B); it reads the changed files and writes `out/findings.json`.

## When to use

Load this skill when you need to review a diff or a set of changed files and produce structured,
deduplicated findings (severity / category / file / line / evidence / recommendation / confidence).

## Rule coverage

Findings come from deterministic scanners, not from the model, so results are reproducible:

| Category | Tool |
|---|---|
| security | bandit, semgrep |
| secret_leakage | detect-secrets |
| async_errors | ruff (ASYNC rules) |
| resource_leak | ruff (SIM115 / bugbear) |
| db_lifecycle | semgrep (`rules/db_lifecycle.yaml`) |

## Invocation

```
python scripts/run_checks.py --target <dir> [--out out/findings.json]
```

`--target` is a directory containing the changed files. Output conforms to `docs/OUTPUT_SCHEMA.md`.

## Output contract

See `docs/OUTPUT_SCHEMA.md` — the single source of truth. The nine fields
(severity, category, file, line, title, evidence, recommendation, confidence, source) are mandatory.
Secrets in `evidence` are redacted before output.
