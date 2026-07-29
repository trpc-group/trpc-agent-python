# security rules

AST-first checks (`checks/check_security.py`, `CATEGORY="security"`): every rule is confirmed
on the parsed post-image and reported with `precision="high"`. When the AST is unavailable
(diff-only gap reconstruction with `content_complete=False`, or a syntax error) the module
degrades to a per-changed-line regex pass with `precision="low"` / `confidence="low"` and never
raises. Findings anchor only on changed (candidate) lines — a dangerous call that merely
appears in unchanged diff context is never reported.

## Decision quick reference

| rule | what | severity | precision | confidence |
|---|---|---|---|---|
| SEC001 | SQL built by interpolation reaches `execute/executemany/executescript` | critical | high | high (SQL keywords visible) / medium |
| SEC002 | `eval`/`exec` on a dynamic expression | critical (constant literal: medium) | high | high |
| SEC003 | `subprocess.run/call/check_call/check_output/Popen` with `shell=True` | critical (interpolated cmd) / medium (literal or unknown cmd) | high | high / medium |
| SEC004 | `os.system` / `os.popen` | critical (interpolated) / medium (literal or unknown) | high | high / medium |
| SEC005 | `yaml.load` without a safe Loader | high | high | high |
| SEC006 | `pickle.load(s)` / `marshal.load(s)` / `shelve.open` | high | high | medium (payload may be trusted) |
| SEC007 | `requests`/`httpx` call with `verify=False` | high | high | high |
| SEC008 | `tempfile.mktemp` | medium | high | high |

Regex-fallback findings keep the rule id and severity grading but always carry
`precision="low"`, `confidence="low"` and a `(regex fallback)` title suffix.

## SEC001 — SQL injection via dynamically built query

**Detects**: a `Call` whose dotted function name ends in `execute`, `executemany` or
`executescript` and whose first argument is built dynamically per `common.has_interpolation`
(f-string, `+` concatenation with a string side, `%` formatting, `.format(...)`). When the
first argument is a bare name, the check traces bindings one step up inside the same scope:
an interpolated assignment (`query = f"..."`), or a `+=` chain appending non-constant parts
onto a string, marks the variable dynamic; the evidence then quotes both lines
(`L11: query = ... -> L15: cur.execute(query)`).

**Why dangerous**: attacker-controlled text spliced into SQL becomes SQL. `name = "x' OR
'1'='1"` turns a lookup into a table dump; a `;` appended to a numeric id can drop tables on
drivers that allow multi-statements. This is OWASP A03 and routinely a full-database
compromise.

**Reported**:

```python
cur.execute(f"SELECT id FROM users WHERE name = '{name}'")          # f-string

query = "DELETE FROM users WHERE id = " + str(user_id)              # + concatenation,
cur.execute(query)                                                  # traced one step up

q = "SELECT * FROM t WHERE name = '"
q += name                                                           # += chain onto a string
cur.execute(q)
```

**Not reported** (explicit negatives):

```python
cur.execute("SELECT id FROM users WHERE id = ?", (uid,))            # parameterized literal
q = "UPDATE users SET name = ? WHERE id = ?"                        # literal via variable
cur.execute(q, (new_name, uid))
q = f"bad {x}"
q = "SELECT 1"                                                      # full rebind to a literal
cur.execute(q)                                                      # resets the trace: clean
cur.execute(build_query(uid))                                       # helper call: opaque, silent
model.executor.submit(...)                                          # name does not end in execute*
```

**Fix**: keep the SQL text static with placeholders and pass values separately. The
`fix_snippet` is generated from the matched expression, e.g.
`cur.execute('SELECT id FROM users WHERE name = %s', (name,))` (sqlite3 uses `?`, most other
DB-API drivers `%s`).

## SEC002 — eval / exec

**Detects**: calls to the bare builtin `eval`/`exec` (or `builtins.eval/exec`). A non-literal
argument is critical; `eval("constant literal")` is downgraded to medium (no injection
channel, still a smell that hides code from linters and type checkers).

**Why dangerous**: `eval`/`exec` on data is arbitrary code execution in-process — file system,
network and secrets included. There is almost always a safer primitive.

**Reported**:

```python
result = eval(user_input)          # critical
exec(request.form["snippet"])      # critical
value = eval("1 + 2")              # medium: constant, inline it instead
```

**Not reported**: `ast.literal_eval(text)` (safe literal parser); attribute calls such as
`model.eval()` (torch) or `df.eval("a + b")` (pandas) — only the bare builtin name matches.

**Fix**: `ast.literal_eval` for Python literals, `json.loads` for data, an explicit dispatch
table (`handlers[name](...)`) for behaviour selection.

## SEC003 — subprocess with shell=True

**Detects**: `subprocess.run/call/check_call/check_output/Popen` (alias- and
from-import-aware) with the keyword `shell=True` (literal `True` only). Interpolated command
(directly or via the one-step variable trace) is critical; a pure string literal or an
unresolvable variable is medium.

**Why dangerous**: with a shell in the loop every metacharacter in the command string is live:
`f"ping {host}"` with `host = "8.8.8.8; rm -rf ~"` runs both commands. Even literal commands
inherit `$PATH`/IFS ambiguity they do not need.

**Reported**:

```python
subprocess.run(f"ping {host}", shell=True)                 # critical
subprocess.check_output("ls -la /tmp", shell=True)         # medium: literal, still drop the shell
```

**Not reported**: argv-list calls (`subprocess.run(["ping", host])`), `shell=False`, and
`shell=flag` where the flag is not a literal `True` (not provably unsafe).

**Fix**: pass an argument list and drop `shell=True`. The `fix_snippet` splits the real
command: `subprocess.run(['ping', host])`; use `shlex.split`/`shlex.quote` when a shell is
truly required.

## SEC004 — os.system / os.popen

**Detects**: `os.system(...)` / `os.popen(...)`; graded like SEC003 (interpolated command
critical, literal medium).

**Why dangerous**: both always run through the shell — there is no argv form — so any
interpolated variable is a command-injection vector, and the return value hides errors.

**Reported**:

```python
os.system("rm -rf " + path)        # critical
os.popen(f"cat {fname}")           # critical
os.system("sync")                  # medium
```

**Not reported**: other `os` functions (`os.remove`, `os.path.*`); `subprocess` usage
(SEC003's scope).

**Fix**: `subprocess.run(['rm', '-rf', path], check=True)`; for `os.popen` output use
`subprocess.run([...], capture_output=True, text=True).stdout`.

## SEC005 — unsafe yaml.load

**Detects**: `yaml.load(...)` with no `Loader` argument (keyword or second positional), or
with `Loader`/`UnsafeLoader` (and their C variants).

**Why dangerous**: the default/unsafe loaders resolve `!!python/object` tags, so a YAML
document can instantiate arbitrary Python objects — deserialization RCE from a config file.

**Reported**:

```python
cfg = yaml.load(fh)                              # no loader
cfg = yaml.load(fh, Loader=yaml.UnsafeLoader)    # explicitly unsafe
```

**Not reported**: `yaml.safe_load(fh)`; `Loader=yaml.SafeLoader/BaseLoader/FullLoader` (and C
variants); unknown custom loader classes get the benefit of the doubt (precision first).

**Fix**: `yaml.safe_load(fh)` — the `fix_snippet` rewrites the matched call.

## SEC006 — pickle / marshal / shelve deserialization

**Detects**: `pickle.load(s)`, `marshal.load(s)`, `shelve.open` on a changed line.
`confidence="medium"` on purpose: the payload may be trusted local data (e.g. a cache this
same program wrote) and static analysis cannot see provenance — this is a "verify the trust
boundary" finding, not a certain vulnerability.

**Why dangerous**: unpickling executes `__reduce__` payloads; a single attacker-supplied blob
is arbitrary code execution. `shelve` is pickle-backed, `marshal` is not hardened against
hostile input.

**Reported**:

```python
obj = pickle.loads(request.data)   # classic RCE if data crosses a trust boundary
db = shelve.open(path)
```

**Not reported**: `json.loads(...)` and other text codecs; `pickle.dumps` (serializing is not
the risk); unchanged pre-existing usage in context lines.

**Fix**: switch to a data-only format (`json.loads(blob)`) where possible; otherwise document
and enforce the trust boundary (sign payloads that cross it).

## SEC007 — TLS verification disabled

**Detects**: a call with keyword `verify=False` whose target resolves to `requests` or `httpx`
— module calls (`requests.get`), from-imports, aliased imports, client constructors
(`httpx.Client(verify=False)`), or a receiver traced one step to
`s = requests.Session()` / `httpx.Client()` in the same scope.

**Why dangerous**: without certificate verification any on-path attacker can impersonate the
server; credentials and tokens sent over that channel are silently interceptable. "Temporary"
`verify=False` lines are notorious for reaching production.

**Reported**:

```python
requests.get(url, verify=False)
s = requests.Session()
s.get(url, timeout=5, verify=False)
```

**Not reported**: `verify=True` or a CA-bundle path (`verify="/etc/ca.pem"`); `verify=False`
on a receiver that cannot be resolved to requests/httpx (`checker.get(item, verify=False)`
may be an unrelated API and is deliberately skipped).

**Fix**: re-enable verification (`verify=True`) or pin the internal CA bundle
(`verify='/path/to/ca.pem'`).

## SEC008 — tempfile.mktemp

**Detects**: any call to `tempfile.mktemp` (deprecated since Python 2.3).

**Why dangerous**: `mktemp` only returns a free name; between the name check and your `open`
another process can create that path (symlink attack) and make the program write through it —
a classic TOCTOU privilege-escalation primitive on shared machines.

**Reported**: `path = tempfile.mktemp(suffix=".log")`.

**Not reported**: `tempfile.mkstemp()`, `tempfile.NamedTemporaryFile()`,
`tempfile.TemporaryDirectory()` — these create the file/directory atomically.

**Fix**: `fd, path = tempfile.mkstemp(suffix='.log')` (the snippet carries the original
arguments over) or `NamedTemporaryFile(delete=False)`.

## Diff-only robustness

For `content_complete=False` files the gap-reconstructed post-image often fails to parse
(indented fragments at module level); `parse_ast()` returns `None` and the check switches to
per-changed-line regexes: attribute-form f-string `execute`, bare `eval/exec` (lookbehind
excludes `.eval` and `literal_eval`), `subprocess.* ... shell=True`, `os.system/popen`,
`yaml.load` (vetoed by `SafeLoader|BaseLoader|FullLoader|safe_load` on the line),
`pickle/marshal/shelve`, `verify=False` (requiring `requests.`/`httpx.` on the same line) and
`mktemp`. Comment lines are skipped, one finding per line, and nothing in the module ever
raises — a total scan failure degrades to the regex pass file by file.
