# Missing Test Rule

Detects source code changes that lack corresponding test file changes.

## Trigger

Fires when a diff contains added lines in `.py` source files but no changes in:
- Files under `tests/` directories
- Files whose name starts with `test_` or ends with `_test.py`

## Findings

| Condition | Severity | Confidence | Guidance |
|-----------|----------|------------|----------|
| Source .py changed, no test files touched | medium | 0.8 | Add or update unit tests covering the changed behavior. |

## Rationale

Every behavior change should include or reference a test. Without test coverage,
regressions go undetected.

## Remediation

Add or update unit tests that exercise the changed code paths.
