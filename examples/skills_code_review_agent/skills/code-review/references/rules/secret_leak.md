# Secret Leak Rule

Detects hardcoded secrets (API keys, passwords, AWS keys) in source code.

## Detection

Uses the `secret_patterns` module which checks for:
- OpenAI-style keys (`sk-*`)
- Generic API key assignments
- Password assignments
- AWS access key patterns (`AKIA*`)
- Private key headers (`-----BEGIN ... PRIVATE KEY-----`)

## Findings

| Condition | Severity | Confidence | Guidance |
|-----------|----------|------------|----------|
| Any recognized secret pattern in added source line | critical | 0.95 | Remove the secret, rotate it, and load it from environment variables or a secret manager. |

## Evidence Redaction

All evidence strings are redacted before output; raw secrets never appear in findings.
The evidence field will contain `***REDACTED-{type}` markers.

## Remediation

1. Remove the secret from the source immediately.
2. Rotate (revoke and reissue) the exposed credential.
3. Load secrets at runtime from environment variables, a `.env` file (never committed),
   or a secrets manager.
