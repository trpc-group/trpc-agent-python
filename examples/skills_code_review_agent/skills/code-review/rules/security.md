# Security Rules

## Rule matrix

| Rule ID | Detection pattern | Severity | Confidence |
| --- | --- | ---: | ---: |
| `rule:dangerous-exec` | Added `eval(...)` or `exec(...)` | high | 0.90 |
| `rule:command-injection` | Added `os.system(...)` or `os.popen(...)` | high | 0.86 |
| `rule:shell-injection` | `subprocess.*(..., shell=True)` | high | 0.88 |
| `rule:sql-injection` | `execute()` receives an f-string, `.format()`, concatenation, or `%`-built SQL | high | 0.86-0.90 |
| `rule:tls-verification` | `requests`/`httpx` call contains `verify=False` | medium | 0.80 |
| `rule:unsafe-deserialization` | `yaml.load` without `SafeLoader`, or `pickle.load(s)` | medium/high | 0.82/0.86 |
| `rule:weak-hash` | `hashlib.md5/sha1`; credential names raise the severity | medium/high | 0.82/0.90 |

Sandbox-script equivalents include `skill-script:dangerous-exec`, `skill-script:command-injection`, `skill-script:shell-injection`, and `skill-script:sql-injection`.

## Recommended fixes

- Replace dynamic execution with a constrained parser or explicit dispatch table.
- Pass an argv list to `subprocess` with `shell=False`; validate every user-controlled element.
- Use database-driver placeholders and pass values separately.
- Keep certificate verification enabled and configure a trusted CA bundle when necessary.
- Use `yaml.safe_load`; never unpickle untrusted bytes.
- Use Argon2, scrypt, or bcrypt for passwords and SHA-256+ for integrity. Mark an MD5/SHA-1 non-security checksum with `usedforsecurity=False` when supported.

## Known false positives and noise controls

- A constant f-string such as `f"SELECT 1"` and correctly parameterized SQL can resemble interpolation. `ctx.sql_safe` verifies the AST or hunk before suppressing it.
- `shell=True` may be intentional for a fixed administrative command, but it remains high risk and requires human review rather than silent suppression.
- Pickle used only with authenticated, trusted local data still creates a dangerous trust boundary; document it if an alternative format is impossible.
- MD5/SHA-1 used as a protocol-required checksum is demoted when it is not protecting a credential; prefer an explicit non-security marker.
