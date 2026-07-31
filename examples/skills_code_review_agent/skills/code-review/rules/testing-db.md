# Testing And Database Lifecycle Review Rules

## Test Coverage For Behavior Changes

Rule: changes that add branches, error handling, security checks, parsing logic,
or persistence behavior should include focused tests for success and failure
paths.

Example:

```python
def parse_token(value):
    return value.split(":", 1)[1]
```

Trigger conditions:

- Source files change but no nearby test, fixture, or snapshot changes appear.
- New error handling lacks negative tests.
- Security, parser, or database behavior changes without regression fixtures.

## Database Transactions

Rule: transactions must commit, roll back, and release connections deterministically.

Example:

```python
conn = pool.acquire()
conn.execute("insert into audit values (?)", [value])
return True
```

Trigger conditions:

- Changed code opens sessions/connections/cursors without context managers or
  finally cleanup.
- Transactions can return or raise before commit/rollback.
- SQL uses string interpolation with untrusted values.

## Migration And Schema Safety

Rule: schema changes must be reversible or compatible with existing data and
must not silently drop columns, tables, indexes, or constraints.

Example:

```sql
DROP TABLE findings;
```

Trigger conditions:

- Destructive DDL appears without migration notes or tests.
- New non-null columns lack defaults or backfill logic.
- Index or constraint changes can alter query semantics.
