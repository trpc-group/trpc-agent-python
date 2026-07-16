# Database Lifecycle Rule

Detects database connections, cursors, and transactions opened without proper lifecycle management.

## Patterns

| Pattern | Severity | Confidence | Guidance |
|---------|----------|------------|----------|
| `.connect()` without context manager or close() | high | 0.8 | Use `with ...connect(...) as conn:` or close the connection in a finally block. |
| `.cursor()` without context manager | low | 0.5 | Close the cursor or use a context manager. |
| `.commit()` without rollback handling | medium | 0.65 | Wrap the transaction in try/except and roll back on failure. |

## Rationale

Unclosed database connections leak file descriptors and connection pool slots.
Transactions committed without rollback handling can leave data in an inconsistent
state on error.

## Remediation

- Use context managers (`with conn:` / `with conn.cursor()`) wherever possible.
- Always wrap transactions in `try/except` and call `rollback()` on failure.
