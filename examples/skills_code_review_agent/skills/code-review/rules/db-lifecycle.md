# Database lifecycle rules

## Detection contract

| Rule ID | Reports when an added assignment has no visible finalizer in the same hunk | Severity | Confidence |
|---|---|---:|---:|
| `db.connection-without-close` | a supported driver `.connect(...)` result lacks `.close()` | medium | 0.76 |
| `db.transaction-without-finalize` | `name = object.begin(...)` lacks `name.commit()` or `name.rollback()` | high | 0.82 |

Supported connection prefixes are `sqlite3`, `psycopg`/`psycopg2`, `pymysql`,
`mysql.connector`, `mysql`, and `db`.

## Scope and confidence

The detector examines executable added Python lines, extracts a simple assigned
variable, and searches the same hunk for the matching finalizer. Connection
results stay in human review at confidence 0.76. An explicit transaction with
no visible outcome reaches formal findings at confidence 0.82.

## Examples

### Reports

```python
connection = sqlite3.connect(path)
transaction = session.begin()
```

### Stays quiet

```python
connection = sqlite3.connect(path)
try:
    use(connection)
finally:
    connection.close()

transaction = session.begin()
try:
    update()
    transaction.commit()
except Exception:
    transaction.rollback()
    raise
```

## Remediation

Prefer connection and transaction context managers. Otherwise close
connections in `finally`, commit only after success, and roll back every
failure path before propagating the error.

## Blind spots

Framework-managed sessions, dependency injection, wrapper factories, aliases,
nested transactions, async drivers, and lifecycle operations outside the hunk
are not modeled. A visible commit or rollback on one branch can suppress a
report without proving that every path is finalized.
