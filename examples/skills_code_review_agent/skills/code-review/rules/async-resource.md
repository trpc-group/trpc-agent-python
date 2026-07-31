# Async And Resource Lifecycle Review Rules

## Await And Cancellation Correctness

Rule: asynchronous code must await coroutines, preserve cancellation, and avoid
blocking the event loop with file, network, subprocess, or CPU-heavy work.

Example:

```python
async def handle():
    client.fetch(url)
    except Exception:
        return None
```

Trigger conditions:

- Changed lines call known async APIs without `await` or task management.
- `CancelledError` can be swallowed by broad `except Exception` or `except BaseException` paths.
- Synchronous file, HTTP, sleep, subprocess, or database calls are added inside
  `async def` without an executor or async alternative.

## Resource Ownership

Rule: files, streams, locks, subprocesses, HTTP clients, database sessions, and
temporary directories must be closed or cleaned up on success and failure paths.

Example:

```python
client = httpx.AsyncClient()
return await client.get(url)
```

Trigger conditions:

- Changed code creates resources without a `with`, `async with`, `try/finally`,
  explicit close, or lifecycle owner.
- New subprocesses lack timeout and cleanup.
- Error paths return early before cleanup.

## Bounded Work

Rule: new loops, retries, streaming reads, subprocess calls, and network calls
must have bounded timeout, retry, and output behavior.

Example:

```python
while True:
    await queue.get()
```

Trigger conditions:

- Infinite loops, unbounded retries, or large reads are introduced.
- Timeouts are removed or set only at a higher layer not visible in the diff.
