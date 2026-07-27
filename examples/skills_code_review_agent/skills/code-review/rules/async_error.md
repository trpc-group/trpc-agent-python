# Async Error and Client-Lifecycle Rules

## Rule matrix

| Rule ID | Detection pattern | Severity | Confidence |
| --- | --- | ---: | ---: |
| `rule:async-session-lifecycle` | `aiohttp.ClientSession(...)` outside `async with` | high | 0.88 |
| `rule:async-client-lifecycle` | `httpx.AsyncClient(...)` outside `async with` | high | 0.86 |
| `rule:async-task-lifecycle` | Bare `asyncio.create_task(...)` whose handle is not retained | medium, manual review | 0.72 |

Sandbox-script equivalents are `skill-script:async-session-lifecycle` and `skill-script:async-client-lifecycle`.

## Recommended fixes

- Use `async with aiohttp.ClientSession() as session` or `async with httpx.AsyncClient() as client`.
- For a longer-lived client, own it at application scope and close it in a guaranteed shutdown/finally path.
- Retain background tasks in a set, await them during shutdown, and attach error handling or `add_done_callback` so exceptions are observed.

## Known false positives and noise controls

- A session assigned on one line may be closed later in the same function or `finally`. `ctx.resource_closed` uses AST scope, then hunk context as a fallback, to suppress the line-level match.
- A task may be retained via assignment, `append`, `add`, or `add_done_callback`. `ctx.task_retained` suppresses those patterns.
- Framework-managed application clients can outlive a function by design. Keep ownership and shutdown visible in the reviewed diff; otherwise the item correctly remains for human review.
