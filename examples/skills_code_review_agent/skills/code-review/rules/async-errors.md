# Async error rules

## Detection contract

| Rule ID | Reports when an added line inside a visible async function contains | Severity | Confidence |
|---|---|---:|---:|
| `async.blocking-time-sleep` | `time.sleep(...)` | high | 0.84 |
| `async.unawaited-coroutine` | `asyncio.sleep(...)` or a visible async function call without await/scheduling | medium | 0.76 |

Scheduling recognized by the heuristic includes `create_task`,
`ensure_future`, and `gather` on the same code line.

## Scope and confidence

The detector follows indentation within each Python hunk. It learns async
function names from visible new-side hunk content, masks comments and strings,
and reports only added lines. The unawaited rule remains below the formal
finding threshold because lifecycle or scheduling may be established elsewhere.

## Examples

### Reports

```python
async def refresh():
    time.sleep(1)
    asyncio.sleep(1)
```

### Stays quiet

```python
async def refresh():
    await asyncio.sleep(1)
    task = asyncio.create_task(load_data())
    message = "time.sleep(1) would block here"
```

## Remediation

Replace blocking waits with `await asyncio.sleep(...)` or move truly blocking
work to a controlled executor. Await a coroutine directly or schedule it with
explicit task ownership, cancellation, and exception handling.

## Blind spots

The heuristic does not build a control-flow graph. Decorators, aliases,
callbacks, returned coroutines, task groups, framework scheduling, and async
functions defined outside visible hunks are not modeled. It also cannot prove
that a created task is eventually awaited or inspected.
