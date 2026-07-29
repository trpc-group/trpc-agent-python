# async rules

AST-first check (`checks/check_async.py`, `CATEGORY="async"`) for event-loop hazards in
Python files: blocking calls on the loop thread, coroutines and tasks whose reference is
lost, and leaked `aiohttp` sessions. Non-Python files, deleted/binary files and files
without candidate lines are skipped; every finding anchors on a changed line
(`ctx.is_changed_line`), so pre-existing code is never re-reported.

## Import resolution

Call targets are resolved through the file's real import statements before matching:

* `import time` / `import time as t` → `t.sleep(...)` resolves to `time.sleep`;
* `from time import sleep` (with or without `as`) promotes the bare name to `time.sleep`;
* a **bare** name with no import binding stays bare — a local helper named `sleep`, `run`
  or `get` never matches a module function;
* an **unaliased dotted** access keeps its spelling (`time.sleep` still matches when the
  import line is invisible in a diff-only gap);
* relative and star imports are ignored.

## Rules

### ASYNC001 — blocking call inside `async def` (high / precision high / confidence high)

Fires when the **own frame** of an `async def` (nested `def`/`lambda` bodies excluded)
contains a call resolving to one of:

`time.sleep`, `requests.get|post|put|delete|head|request`, `urllib.request.urlopen`,
`subprocess.run|call|check_call|check_output`, `socket.create_connection`.

The fix snippet is built from the call's real arguments: `time.sleep(2)` →
`await asyncio.sleep(2)`; `requests.*` → an `httpx.AsyncClient` block; `urlopen` → an
`aiohttp.ClientSession` block; `subprocess.*` → `await asyncio.to_thread(...)`;
`socket.create_connection` → `await asyncio.open_connection(...)`.

Not reported: calls inside a nested sync `def` or `lambda` (they usually run via
`run_in_executor`/`to_thread`), attribute calls on other objects (`session.get(...)`,
`self.queue.get(...)`), and unimported bare names.

### ASYNC002 — coroutine created but never awaited (high / high / high)

Fires on a **bare expression statement** whose call target is

* an `async def` defined in this file (bare name, or `self.<name>` for an async method
  defined in a class of this file), or
* `asyncio.sleep(...)`.

Such a statement only builds a coroutine object and drops it — the body never runs
(`RuntimeWarning: coroutine ... was never awaited`). Fix: `await <call>`.

Not reported: `await coro()`; coroutines passed **as arguments** to `asyncio.run`,
`create_task`, `gather`, `ensure_future`, `TaskGroup.create_task`, … (only the
statement-level call is inspected — an argument position is ownership transfer);
assigned (`fut = coro()`) or returned coroutines; bare-name matching assumes the
`async def` is not rebound.

### ASYNC003 — task reference discarded (medium / high / high)

Fires on a bare expression statement calling `asyncio.create_task(...)`,
`asyncio.ensure_future(...)`, `<loop>.create_task(...)` or
`asyncio.get_event_loop().create_task(...)`. The event loop keeps only a *weak*
reference; the CPython docs require saving the result or the task may be
garbage-collected mid-flight. The fix snippet shows the documented pattern
(`background_tasks.add(task)` + `add_done_callback(discard)`).

A base object counts as a loop only when its name was assigned from
`asyncio.get_event_loop / get_running_loop / new_event_loop` or ends in `loop`.
Not reported: `task = create_task(...)` assignments, tasks appended to a container or
passed to `gather` (argument position), awaited spawns, and `tg.create_task(...)` on a
`TaskGroup` — the group holds strong references, discarding its return value is fine.

### ASYNC004 — sync `open()` in an awaiting `async def` (medium / precision **low** / confidence medium)

Heuristic: the builtin bare `open(...)` appears in the own frame of an `async def` that
also suspends (`await` / `async for` / `async with` in the same frame) — file IO then
runs on the event-loop thread between suspension points. Low precision by design; the
decision table routes this tier into the warnings bucket.

Not reported: dotted opens (`aiofiles.open`, `io.open`, `self.open`); `open` inside
nested sync defs; async functions that never suspend; files where an import rebinds the
name `open`.

### ASYNC005 — `aiohttp.ClientSession()` neither scoped nor closed (high / high / high)

Fires when a function (sync or async) creates a session via a statement-level pattern —
`s = aiohttp.ClientSession()` (plain-name target) or a bare `aiohttp.ClientSession()`
expression statement — and the whole function subtree (nested defs included) shows **no
release**: no `s.close()` / `s.aclose()`, no `with`/`async with s`, and no ownership
transfer.

Ownership transfer suppresses the finding (deliberately not reported): the session is
returned or yielded, stored into an attribute or subscript (`self.session = s`,
`cache[k] = s`), passed to any callable as an argument, or aliased to another name.
Also silent: sessions created directly in a `with`/`async with` item, sessions inside a
larger expression (argument/container/return — the owner is elsewhere), and module-level
creations (long-lived singleton pattern; only function bodies are scanned).

## Diff-only robustness

When `parse_ast()` fails (`content_complete=False` gap reconstruction or a syntax error)
the check degrades to a per-changed-line regex pass with an indentation-based
`async def` scope tracker: blank gap lines and comments neither open nor close scopes,
and changed lines whose enclosing header is hidden in a gap are skipped rather than
guessed. Only ASYNC001 (blocking call while the innermost visible header is
`async def`), ASYNC002 (bare `asyncio.sleep(...)` / bare call of a regex-visible
`async def` name) and ASYNC003 (bare `asyncio.create_task` / `...loop.create_task` /
`ensure_future` statement) survive in this mode, all with precision=low /
confidence=low. The check never raises.
