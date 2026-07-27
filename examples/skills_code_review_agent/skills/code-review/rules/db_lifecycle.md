# Database Lifecycle Rules

## Rule matrix

| Rule ID | Detection pattern | Severity | Confidence |
| --- | --- | ---: | ---: |
| `rule:db-connection-lifecycle` | Assignment from `sqlite3`, `psycopg2`, `pymysql`, or `aiomysql.connect(...)` without visible scope | high | 0.86 |
| `rule:db-session-lifecycle` | Bare assigned `Session()` outside a context manager | medium | 0.75 |

The sandbox equivalent is `skill-script:db-connection-lifecycle`.

## Recommended fixes

- Use the driver's context manager when it defines the required commit/rollback behavior.
- Otherwise wrap connection/session ownership in `try/finally`, close on every path, commit only on success, and roll back on failure.
- For request-scoped ORM sessions, use framework dependency/lifespan hooks and make transaction boundaries explicit.

## Known false positives and noise controls

- A connection assigned on one line may be closed later in the same scope. `ctx.resource_closed` suppresses the finding only with visible close evidence.
- A pool owns physical connections differently from a raw connection. Prefer pool-specific acquisition context managers so ownership is unambiguous.
- A repository object may intentionally own a long-lived session; if lifecycle code is outside the patch, the medium-confidence item remains for human review.
