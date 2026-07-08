# Code Review Rules

This skill ships deterministic checks that are intentionally explainable and easy to audit.

## Covered Categories

- Security risks: dynamic `eval` or `exec`, `subprocess(..., shell=True)`, string-built SQL, unsafe YAML loading and pickle deserialization.
- Asynchronous errors: unscoped `aiohttp.ClientSession` and unobserved `asyncio.create_task`.
- Resource leaks: `open()` without a context manager and predictable temporary files.
- Testing gaps: production Python changes without a corresponding test diff.
- Sensitive information leakage: API keys, tokens, passwords, private keys, bearer credentials and common provider key formats.
- Database lifecycle: raw DB connections or ORM sessions created without scoped close, commit or rollback.

## Noise Control

The agent deduplicates on `(file, line, category)`. Findings below confidence 0.8 become warnings or manual-review items. The test-gap rule is advisory because a hidden test suite or generated tests can exist outside the patch.

