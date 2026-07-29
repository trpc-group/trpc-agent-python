# db_lifecycle rules

AST-first, function-scope check (`checks/check_db_lifecycle.py`, `CATEGORY="db_lifecycle"`).
It tracks database connections, cursors and explicit transactions created inside a function
and reports lifecycle gaps: never closed, closed on the happy path only, or written to
without a commit. Findings anchor on the resource-opening (or begin/execute) line, and that
line must be part of the diff (`ctx.is_changed_line`) — introducing a leak by *deleting* a
`close()` on an unchanged open line is out of scope by design.

## Category boundary with resource_leak

The split is by **object type**, not by mechanism: database objects — connections, cursors,
transactions — are reported here and only here; files, sockets, locks, subprocesses and
thread pools belong to `resource_leak`. Both checks use the same ownership-transfer
philosophy but are implemented independently (check modules never import each other), so a
driver connection is never double-reported by both categories.

## Connection detection

`.connect(...)` calls of the known driver modules `sqlite3`, `psycopg2`, `pymysql`,
`MySQLdb`, `mysql.connector`, `cx_Oracle`, `pyodbc` — import-alias aware
(`import sqlite3 as sq` → `sq.connect`, `from psycopg2 import connect as pg_connect`).
A bare `connect(...)` counts only while one of those modules is imported **and** the name
`connect` is not locally rebound (`def connect`, `connect = ...`, `from othermod import
connect` all shadow it and silence the rule).

## Rules

| rule  | fires when                                                               | severity | precision | confidence |
|-------|--------------------------------------------------------------------------|----------|-----------|------------|
| DB001 | connection assigned to a local, no `var.close()`, no `with`, no transfer | high     | high      | high       |
| DB002 | `var.cursor()` assigned to a local, no close / `with` / transfer         | medium   | high      | medium     |
| DB003 | literal INSERT/UPDATE/DELETE/REPLACE/CREATE/DROP/ALTER, no commit        | high     | low       | medium     |
| DB004 | `x.begin()` or `execute("BEGIN...")`, no commit/rollback in the function | high     | high      | high       |
| DB005 | close exists but not in `finally`, with a can-raise call before it       | medium   | low       | medium     |

### DB001 — connection never closed

Assignment forms tracked: `conn = <driver>.connect(...)` and the annotated variant
(`conn: Connection = ...`), in function scope only. The first open of a variable wins; a
re-open of the same name does not double-report. Fix snippet is built from the real
assignment line: `with contextlib.closing(<real call>) as conn:` — deliberately `closing()`
rather than `with conn:`, because for sqlite3/psycopg2 the connection context manager
commits but does **not** close.

### DB002 — cursor never closed

Any `var.cursor()` assignment, same ownership analysis as DB001. Severity is only medium
and confidence medium because most DB-API drivers reclaim cursors on GC — the real harm is
exhausted server-side handles under load, not a hard leak. A cursor that *is* closed
somewhere (even outside a `finally`) is never escalated to a DB005-style complaint: the GC
backup makes that nagging noise.

### DB003 — literal write without commit

Only string-literal SQL counts (`ast.Constant` first argument of `.execute()` /
`.executemany()`, or the leading constant chunk of an f-string); dynamic SQL is invisible
on purpose. SELECT / PRAGMA never fire, and `executescript()` is skipped (sqlite3
auto-commits before running it). Suppressed by any of:

* a `.commit()` or `.rollback()` call (method or bare name) anywhere in the function;
* `execute("COMMIT"/"ROLLBACK"/"END")` literals;
* a transaction-managing `with`: `with conn:`, `with self.conn:`,
  `with <driver>.connect(...) as conn:`, `with engine.begin():` — note that
  `with contextlib.closing(conn)` does **not** suppress (closing never commits);
* the word `autocommit` anywhere in the function source (connection configured for
  autocommit);
* DB004 having fired in the same function (one transaction complaint, not two).

`precision=low` is honest: autocommit configured outside the function (connection factory,
framework session) is invisible to a per-function analysis, so the triage table routes
DB003 into the warnings bucket unless an LLM confirms it. One finding per function; the
evidence carries the total write count.

### DB004 — dangling explicit transaction

`x.begin()` (any receiver, but not when it is the context expression of a `with` — SQLAlchemy's
`with engine.begin():` commits by itself) or `execute("BEGIN...")`, with no
commit/rollback evidence anywhere in the function. Reported once per function at the first
changed begin line. DB004 subsumes DB003 in the same function.

### DB005 — close not exception-safe

Variant of DB001 and mutually exclusive with it: it only fires when a `var.close()`
*exists*. Risky shape: no close sits inside a `finally` block, the variable is not
`with`-managed, and at least one other call — i.e. a statement that can raise — sits
strictly between the connect line and the first close line. Any intervening `Call` counts,
even logging, hence `precision=low`.

## Ownership transfer — deliberately NOT reported

A tracked variable stays silent when, in the same function, it is:

* **returned or yielded directly**: `return conn`, `return conn, cur`, `yield conn` —
  but NOT `return cur.rowcount`: attribute/method results do not carry the resource;
* **stored or aliased**: `self.conn = conn`, `pool[key] = conn`, `conns = [conn]`,
  `other = conn` (assignment values expose the name directly);
* **passed as an argument to any call**: `register(conn)`, `contextlib.closing(conn)`,
  `atexit.register(conn.close)`, even `log.info("%s", conn)` — recall is traded for
  precision here. Receiver position (`conn.cursor()`, `conn.execute(...)`) is not an
  argument and does not transfer;
* **used as a context manager** (`with conn:`) — commits rather than closes on most
  drivers, but flagging the idiomatic form would be noise;
* **declared `global`** — module lifetime is managed elsewhere.

Also never reported: module/class-level connections (no enclosing function — global
singletons are conventional); `with <driver>.connect(...) as conn:` (never tracked, it is
not an `Assign`); `with contextlib.closing(x.cursor()) as cur:` (the cursor is a call
argument); and close/transfer evidence inside *nested* defs still suppresses the outer
finding (a cleanup closure counts as handling).

## Diff-only robustness

When `parse_ast()` fails (gap-reconstructed partial content, syntax error) the module never
raises. It degrades to a conservative per-changed-line regex that reports only DB001-style
`var = <driver>.connect(...)` lines with no visible `var.close()` / `with var` /
`return var` / `yield var` anywhere in the visible text, at `precision=low` /
`confidence=low`. DB002–DB005 need real scope analysis and are skipped entirely in that
fallback.
