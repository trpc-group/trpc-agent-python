# Code Review Rules

The deterministic dry-run rule engine mirrors the `code-review` Skill policy without calling a real model.

## Covered categories

- `secrets`: API keys, tokens, passwords, private keys, authorization headers, cookies, and credential URLs in added lines.
- `security`: dynamic execution (`eval`, `exec`), subprocess shell execution, and SQL string interpolation.
- `async`: untracked `asyncio.create_task(...)` calls and async client sessions without context management.
- `resource_leak`: file handles opened without a context manager and persistent temporary files without visible cleanup.
- `database_lifecycle`: database connections, sessions, and transactions without visible close, commit, rollback, or context management.
- `test_coverage`: production Python changes without corresponding test changes; this is routed to warnings / human review because it is heuristic.

## Noise control

Findings must anchor to added changed lines when possible. Low-confidence findings and unanchored issues are warnings rather than high-confidence findings. Duplicate findings for the same file, line, and category are merged.
