# Code Review Rule Catalogue

The deterministic rules are deliberately small, explainable, and auditable. Each finding carries its rule ID in `source`; sandbox-script equivalents use the `skill-script:` prefix. The host deduplicates `(file, line, category)`, applies AST or hunk-context suppressions, and records every suppression in the report.

| Category | Documentation | Typical disposition |
| --- | --- | --- |
| Security | [security.md](security.md) | High-confidence finding |
| Async errors and clients | [async_error.md](async_error.md) | Finding or manual review |
| Resource leaks | [resource_leak.md](resource_leak.md) | Finding, warning, or suppression |
| Database lifecycle | [db_lifecycle.md](db_lifecycle.md) | Finding or suppression |
| Sensitive information | [sensitive_info.md](sensitive_info.md) | Critical finding, always redacted |
| Testing gaps | [testing.md](testing.md) | Manual review |

Confidence determines presentation, not truth. Signals below the confident threshold belong in `warnings` or `needs_human_review`; a context rule may drop or demote a line-level match when the surrounding code proves it safe.
