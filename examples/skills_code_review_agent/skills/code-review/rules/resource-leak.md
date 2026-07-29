# Resource leak rules

## Detection contract

| Rule ID | Reports when an added assignment has no visible finalizer in the same hunk | Severity | Confidence |
|---|---|---:|---:|
| `resource.open-without-close` | `name = open(...)` without `name.close()` | medium | 0.74 |
| `resource.client-session-without-close` | `name = ClientSession(...)` without `name.close()` | medium | 0.78 |

Both rules produce human-review candidates at the locked confidence boundaries.

## Scope and confidence

The detector examines executable new-side Python lines. It masks comments and
strings, extracts a simple assigned variable name, and searches visible lines
in the same hunk for that variable's `.close()` call. It does not infer a
finalizer from another hunk or file.

## Examples

### Reports

```python
handle = open(path)
session = aiohttp.ClientSession()
```

### Stays quiet

```python
handle = open(path)
try:
    consume(handle)
finally:
    handle.close()

async with aiohttp.ClientSession() as session:
    await consume(session)
```

The `async with` example stays quiet because it does not match the simple
assignment constructor.

## Remediation

Prefer `with open(...)` and `async with ClientSession(...)`. When a context
manager is unsuitable, finalize the resource on every path in `finally` and
make ownership transfer explicit.

## Blind spots

The rule does not model aliases, factory functions, reassignment, ownership
transfer, exception paths, or lifecycle code outside the hunk. Conversely, a
visible close in one branch can suppress a report even when another branch
leaks; treat this as a local structural signal, not lifecycle proof.
