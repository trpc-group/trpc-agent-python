# Review Rules

## Detection Categories

The initial implementation covers these categories:

- Security risk
- Async error
- Resource leak
- Missing tests
- Sensitive information leak
- Database transaction or connection lifecycle issue

## Rule Design Principles

- Prefer deterministic, high-signal rules for the first implementation.
- Run rules on structured diff data instead of raw text blobs whenever possible.
- Treat low-confidence heuristics as review aids, not final verdicts.
- Keep evidence small and local so reports stay readable.
- Avoid duplicate findings for the same issue location.

## Category Notes

### Security Risk

High-confidence patterns:

- `eval(...)`
- `exec(...)`
- `pickle.loads(...)`
- `yaml.load(...)` without a safe loader
- `subprocess.*(..., shell=True)`
- TLS verification disabled with `verify=False`

Severity guidance:

- `eval/exec/pickle.loads/shell=True`: high
- `verify=False`: medium to high depending on context

### Async Error

Heuristic patterns:

- Detached `asyncio.create_task(...)` without visible lifecycle tracking
- `except Exception: pass` style swallowing near async flows

Routing guidance:

- Most first-pass async findings should land in `needs_human_review` unless the pattern is obviously dangerous.

### Resource Leak

Heuristic patterns:

- `open(...)` without `with open(...)`
- HTTP/session/client constructors without `with` / `async with`

Routing guidance:

- Resource-lifecycle heuristics are useful but often context-sensitive, so medium-confidence routing is preferred in the first version.

### Missing Tests

Diff-level pattern:

- Production code changes are present but no test files are updated in the same diff.

Routing guidance:

- This category usually goes to `needs_human_review` unless the project later adds stronger ownership or coverage signals.

### Sensitive Information Leak

High-confidence patterns:

- `api_key = "..."`
- `token = "..."`
- `password = "..."`
- `secret = "..."`
- `Bearer ...`
- `AKIA...`
- private key headers such as `BEGIN PRIVATE KEY`

Severity guidance:

- Hard-coded credentials are generally high or critical.

### Database Lifecycle

Heuristic patterns:

- Database connection/session created without visible close handling
- Transaction opened without clear commit / rollback handling

Routing guidance:

- Emit high-confidence findings only when lifecycle handling is clearly absent.
- Otherwise route to `needs_human_review`.

## Finding Contract

Each finding should include at least:

- `severity`
- `category`
- `file`
- `line`
- `title`
- `evidence`
- `recommendation`
- `confidence`
- `source`

## Noise Control

- Do not emit duplicate findings for the same category, file, and line
- Route low-confidence items into warnings or human-review buckets
- Redact secrets before persistence and reporting

## First-Pass Confidence Guidance

- `confidence >= 0.8`: final finding
- `0.4 <= confidence < 0.8`: `needs_human_review`
- `confidence < 0.4`: warning
