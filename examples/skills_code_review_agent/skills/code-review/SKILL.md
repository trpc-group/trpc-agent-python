---
name: code-review
description: Analyze code changes with configurable security, async, database-lifecycle, and testing rules.
---

# Code review

Inputs are a unified git diff or the host agent's normalized review JSON.
Run `runner.py` through the governed workspace executor. The runner loads YAML
rules, parses only added lines, dispatches regex or Python AST detectors, and
returns `Finding[]` as JSON. A matched rule may schedule a validator task; only
the sandbox layer may execute that task.

Capabilities:

- security and sensitive-information detection
- asynchronous task misuse detection
- database and resource-lifecycle detection
- missing-test-change detection

Treat output as candidates, not unquestionable truth: every issue needs a
changed file, changed line, concrete evidence, and a practical recommendation.

Never interpolate review text into a shell command. Do not access the network,
credentials, or paths outside the workspace. See `references/rules.md` for the
rule rationale.
