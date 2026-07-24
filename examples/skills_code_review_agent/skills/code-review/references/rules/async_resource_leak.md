# Async Resource Leak Rule

Detects resource management issues in async Python code.

## Patterns

| Pattern | Severity | Confidence | Guidance |
|---------|----------|------------|----------|
| `aiohttp.ClientSession()` without `async with` | high | 0.8 | Use `async with aiohttp.ClientSession() as session:` or ensure session.close() is awaited. |
| `asyncio.create_task()` without storing reference | medium | 0.7 | Store the task and await/cancel it; unreferenced tasks may be garbage collected mid-flight. |
| `open()` assigned without `with` context manager | medium | 0.7 | Use `with open(...) as f:` so the handle is always closed. |

## Rationale

Resources created inside async functions must be properly managed. Leaked sessions,
fire-and-forget tasks, and unclosed file handles can cause connection pool exhaustion,
memory leaks, and unpredictable behavior under load.

## Remediation

Wrap resources in context managers (`async with` / `with`) or ensure explicit cleanup
in `finally` blocks.
