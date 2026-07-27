# Sensitive-Information Rules

## Rule matrix

| Rule ID | Detection pattern | Severity | Confidence |
| --- | --- | ---: | ---: |
| `rule:sensitive-info` | Credential-bearing assignment, provider-shaped token, private key, bearer token, credential URL, or guarded high-entropy value | critical | 0.98 |
| `skill-script:sensitive-info` | Sandbox corroboration of a live credential shape | critical | 0.98 |
| `skill-script:sensitive-info-marker` | Sandbox sees an upstream `<REDACTED>` marker only | critical, manual review | 0.75 |

Covered shapes include AWS access/secret keys, GitHub and Slack tokens, Stripe/OpenAI-style keys, Google `AIza`, SendGrid, npm, Twilio, Alibaba, JWTs, PEM private keys, and keyword-labelled high-entropy values.

## Recommended fixes

1. Remove the credential from the patch and all generated artifacts.
2. Rotate/revoke it immediately; deleting a Git line does not invalidate a leaked secret.
3. Load the replacement from a secret manager or runtime environment.
4. Purge repository history when policy requires it and review access logs for misuse.

## Known false positives and noise controls

- `os.environ[...]`, `os.getenv(...)`, dotted attribute references, and bare variable references are not literal credentials. `ctx.secret_from_env` and redaction guards suppress them.
- Whole-value placeholders such as `<TOKEN>`, `REPLACE_WITH_YOUR_TOKEN`, and documented examples are excluded. A provider-shaped value is still treated cautiously when the provider publishes a realistic sample.
- UUIDs, Git SHAs, field names, and ordinary hex identifiers are not redacted without credential context and sufficient entropy.
- Evidence is always replaced by `<REDACTED>` before persistence. A literal `<REDACTED>` alone is not proof of a new secret.
