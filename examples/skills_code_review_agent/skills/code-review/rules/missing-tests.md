# Missing-test rule

## Detection contract

| Rule ID | Reports when | Severity | Confidence |
|---|---|---:|---:|
| `tests.missing-coverage` | changed production Python exists and no changed Python test file exists in the change set | low | 0.65 |

The rule emits at most one candidate, anchored to the first changed production
file and line in stable path order.

## Scope and confidence

A test path is a `.py` file under a `tests` directory, starts with `test_`, or
ends with `_test.py`. Deleted-only files, binary files, and test files are not
production candidates. Confidence 0.65 deliberately routes the result to
human review rather than formal findings.

## Examples

### Reports

```text
src/service.py          modified
```

### Stays quiet

```text
src/service.py          modified
tests/test_service.py   modified
```

## Remediation

Add or update a focused test that exercises the changed behavior. If existing
coverage is sufficient, record the relevant test and rationale during human
review rather than raising the candidate's confidence.

## Blind spots

The heuristic does not inspect test content, coverage data, generated tests,
non-Python test suites, unconventional repository layouts, or whether the test
change actually exercises the production change. A changed test file suppresses
the candidate even if that test is unrelated.
