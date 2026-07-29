# resource_leak rules

Data-flow check (`checks/check_resource_leak.py`, `CATEGORY="resource_leak"`): a
**function-level ownership analyzer**. For every `def`/`async def` (plus the module top
level) it tracks local variables assigned from a resource-acquiring call and asks one
question: *does this scope release the resource, hand its ownership to someone else, or
silently drop it?* Only the last case is a finding. The ownership-transfer model is the
false-positive control that makes the high-severity rules trustworthy.

## Acquisition patterns

Import aliases are resolved (`import tarfile as tf` / `from socket import socket` both
work), so matching is on canonical dotted names:

| kind | calls |
|---|---|
| file-like | `open`, `io.open`, `codecs.open`, `os.fdopen`, `gzip.open`, `bz2.open`, `lzma.open`, `tarfile.open`, `zipfile.ZipFile` |
| socket | `socket.socket`, `socket.create_connection` |
| temp file | `tempfile.NamedTemporaryFile`, `tempfile.TemporaryFile` |

Generic `x.open()` is deliberately **not** matched: `webbrowser.open(url)`,
`pathlib.Path.open`, `Image.open` would make the rule guess. `sqlite3.connect` and
friends belong to the `db_lifecycle` category, not here (no cross-category duplicates).

## Release evidence (any one silences RES001/RES003)

1. **with-statement**: the acquisition is a `with` item (`with open(p) as f:`), or the
   variable is later used as one (`with fh:`).
2. **`var.close()` in the same scope.** If that close sits in a `finally` block, the
   resource is safe on every path. If it does *not*, and a `return`/`raise` lies between
   acquisition and close, the finding degrades to RES005 (exception-path leak) instead of
   RES001 — the happy path closes, the early exit leaks.
3. **Ownership transfer** — see next section.

## Ownership transfer — the deliberate not-reported list

A resource whose ownership provably leaves the scope is someone else's to close. All of
these patterns are **explicitly silent**:

* **returned or yielded**: `return fh`, `yield sock` — including inside tuples, lists,
  dicts, ternaries and boolean expressions (`return fh if ok else None`). Note
  `return fh.read()` is *not* a transfer: the data escapes, the handle does not.
* **stored into an object or container**: `self.fh = fh`, `cache[key] = fh`,
  `self.handles = [fh]`. Direct `self.fh = open(...)` is never even registered — the
  instance owns it, and a `close()` in another method is the normal pattern.
* **passed as an argument to any call, however nested**: `sink.register(fh)`,
  `json.load(open(p))`, `with contextlib.closing(fh):`, `shutil.copyfileobj(fh, dst)`.
  This intentionally over-suppresses (`print(file=fh)` also counts): a missed leak costs
  less than a wrong accusation.
* **aliased**: `g = fh` — the alias's lifetime is untrackable, so both names go silent.
* **parked in a global**: assignment to a `global`-declared name, and module-level
  `LOG = open("app.log", "a")` style handles — process-lifetime globals are owned by the
  module, not leaked (RES002/004/006 still apply at top level).
* **referenced inside a nested function or class**: the closure may close it later.
* **untracked shapes** (miss, not noise): class-body assignments, multi-target or
  tuple-unpacking assignments, opens inside comprehensions, and bare unassigned
  `open(p)` expression statements.

## Rules

| id | pattern | severity | precision | confidence |
|---|---|---|---|---|
| RES001 | file/temp handle acquired, never released, ownership never leaves the scope | high | high | high |
| RES002 | chained `open(...).read()` use-and-discard | low | high (regex fallback: low) | medium (fallback: low) |
| RES003 | socket acquired, never released | high | high | high |
| RES004 | `lock.acquire()` with no `.release()` in the scope | high | high | high / medium* |
| RES005 | close exists, but a `return`/`raise` between acquisition and a non-finally close | medium | low | medium |
| RES006 | `NamedTemporaryFile(delete=False)` neither unlinked nor escaping | medium | low | medium |

\* RES004 confidence is `high` when the receiver was assigned from a
`threading`/`multiprocessing` lock constructor somewhere in the file, `medium` when only
the receiver's name is lock-like (`lock`, `mutex`, `sem`, `semaphore` in the last dotted
segment).

Every finding is anchored on the **resource-acquisition line** (or the `acquire()` line),
which must be a changed line (`ctx.is_changed_line`); pre-existing opens whose close was
removed elsewhere are honestly out of reach. Every finding carries a `fix_snippet` built
from the real matched source (a `with`-rewrite, a `try/finally` close, an
`os.unlink(tmp.name)`).

### RES002 details

`open(cfg).read()` works on CPython because refcounting closes the temporary — but not on
PyPy, and not promptly on exception paths. Severity is `low` and confidence `medium` on
purpose. `open(p).close()` is excluded (it releases immediately), and
`json.load(open(p))` is a call-argument transfer, not RES002.

### RES004 details

`with lock:` compiles to no `acquire()` call, so it can never fire. Extra guards against
deliberate designs:

* **cross-method protocols**: if any scope in the file releases the same receiver
  (`def lock(self): self._lock.acquire()` / `def unlock(self): self._lock.release()`),
  the acquire is silent — unless the receiver is a lock constructed locally in the
  acquiring function itself.
* **wrapper methods**: a `self.*` receiver inside a function whose name contains
  `acquire`/`lock`/`enter`/`hold`, or `return self._lock.acquire(...)`, is a delegation
  API, not a leak.
* `pool.acquire()` and other non-lock receivers are skipped unless provably a
  threading lock (constructor seen) or lock-ish by name.

### RES006 details

`delete=False` keeps the file on disk after close. The rule fires only when the scope has
no `os.unlink`/`os.remove`/`.unlink()` call **and** neither the object nor its `.name`
escapes (returning `tmp.name` or passing it to a call transfers cleanup responsibility to
the receiver). An unclosed `delete=False` handle is reported once as RES001 (with a
delete=False note in the evidence), not twice.

## Diff-only robustness

Ownership analysis needs the whole function body: in a gap-reconstructed post-image a
`close()` on an unseen context line is invisible, and claiming `high` precision there
would be fabrication. Therefore:

| content state | what runs |
|---|---|
| `content_complete=True` (repo mode, or a fully added file in a diff) | all six rules |
| partial but the AST still parses | RES002 only (AST-confirmed, line-local pattern) |
| partial and `parse_ast()` fails | RES002 only, per-changed-line regex, precision/confidence `low` |

Half a function cannot prove a leak, so RES001/003/004/005/006 stand down honestly.
Nothing in this module raises on partial content; a crashing file is skipped, never fatal.
