---
name: code-review
description: Test-only code-review Skill whose checker exits nonzero.
version: 0.0.1
entry: scripts/run_checks.py
rules:
  - rules/db_lifecycle.md
sandbox:
  default_runtime: container
  fallback: local
  timeout_s: 5
  max_output_bytes: 8192
  env_whitelist: [PATH, HOME, LANG]
---

# failing code-review Skill

This fixture Skill is intentionally minimal. Its `run_checks.py` exits with a
nonzero status so tests can exercise the real sandbox-failure path without
monkeypatching the runtime.
