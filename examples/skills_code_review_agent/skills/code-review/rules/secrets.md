# Secret rules

## Detection contract

| Rule ID | Reports when text contains | Severity | Confidence |
|---|---|---:|---:|
| `secrets.<secret_type>` | a supported credential format or sensitive assignment | high | 0.91–0.96 |

Supported families include major cloud and source-control tokens, private-key
headers, bearer/JWT credentials, database URLs with credentials, service API
keys, password/token/secret assignments, and high-entropy assigned values. The
same pattern table drives detection and `[REDACTED:<secret_type>]` replacement.

## Scope and confidence

Secrets are language-independent and apply to every text file, including
configuration formats. The detector scans new-side hunk content and deleted
old-side lines. A deleted credential retains its real old line number and
`line_side=old` because removal from the current file does not revoke exposure
from a patch or repository history.

Recognized documentation placeholders are suppressed. Confidence varies by
pattern specificity; no matched secret value is returned by the detector API.

## Examples

### Reports

```text
password = "<synthetic non-placeholder credential>"
Authorization: Bearer <synthetic long token>
-----BEGIN PRIVATE KEY-----
```

The examples describe shapes only. Tests construct synthetic credentials at
runtime so repository scanning and push protection never encounter a usable
literal.

### Stays quiet

```text
password = "changeme"
token = "your-token-here"
api_key = "[REDACTED]"
```

## Remediation

Revoke and rotate the credential; removal alone is insufficient. Load the
replacement from a secret manager or protected environment injection, inspect
history and logs for exposure, and use the provider's incident guidance.

## Blind spots

Split strings, encoded or encrypted values, runtime-generated credentials,
custom formats, low-entropy secrets, and values fetched from external systems
may not match. Placeholder suppression can also hide a real credential that
was deliberately chosen to look like documentation text.
