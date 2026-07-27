# Resource-Leak Rules

## Rule matrix

| Rule ID | Detection pattern | Severity | Confidence |
| --- | --- | ---: | ---: |
| `rule:file-lifecycle` | A variable is assigned `open(...)` outside a `with` statement | medium | 0.78 |
| `rule:tempfile-lifecycle` | `tempfile.mktemp(...)` creates a predictable path | medium | 0.84 |
| `rule:request-timeout` | `requests.get/post/...` has no explicit `timeout=` | medium | 0.80 |

The sandbox emits `skill-script:file-lifecycle` for its corresponding line-level check.

## Recommended fixes

- Use `with open(...) as handle`; if ownership must escape, close it in `finally` and document the owner.
- Replace `mktemp` with `NamedTemporaryFile`, `TemporaryDirectory`, or `mkstemp`, preserving secure creation semantics.
- Pass a bounded connect/read timeout to every outbound request and handle timeout exceptions explicitly.

## Known false positives and noise controls

- A handle opened on one line may be closed later. `ctx.resource_closed` suppresses it only when the same AST scope or hunk proves closure.
- A wrapper may inject a timeout around `requests`. Keep the timeout visible at this call site or route through a named, reviewed client abstraction.
- An `open` result intentionally returned to a caller transfers ownership, but the diff alone may not prove the caller closes it; leave this as a warning/manual-review item.
