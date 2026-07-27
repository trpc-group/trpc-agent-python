# Testing-Gap Rule

## Rule matrix

| Rule ID | Detection pattern | Severity | Confidence |
| --- | --- | ---: | ---: |
| `rule:test-coverage` | A production `.py`/`.pyi` file changes while no recognized test path changes | low, manual review | 0.62 |

Recognized tests include `test/`, `tests/`, `test_*.py`, and `*_test.py` paths. This is an advisory review signal, never a high-confidence defect.

## Recommended fixes

- Add or update focused tests for the changed behavior, including failure and boundary cases.
- If coverage lives in generated, integration, contract, or downstream suites, link that evidence in the review and keep the item as an acknowledged manual decision.
- Prefer a regression test that fails before the production change and passes after it.

## Known false positives and noise controls

- Tests may live outside the submitted patch, use a nonstandard directory, or be generated in CI.
- Documentation, typing-only, or unreachable-code changes may not require a new test.
- Because absence cannot be proven from one diff, this rule always uses `needs_human_review`; it must never enter confident findings or affect the high-risk false-positive metric.
