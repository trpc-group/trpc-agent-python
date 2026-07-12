# Code Review Report

- Task ID: `sample-task`
- Status: `completed_with_warnings`
- Created: `2026-01-01T00:00:00+00:00`
- Completed: `2026-01-01T00:00:00.050000+00:00`
- Repository: `security`
- Scope: `changed`
- Input: `fixture` / `security`

## Summary

Deterministic review of 1 changed file\(s\).

- Findings: `1`
- Warnings: `1`
- Needs human review: `0`
- Severity distribution: `{'critical': 1}`

## Findings

### [CRITICAL] Untrusted data crosses a dangerous execution boundary

- Category: `security`
- Location: `commands.py:4`
- Confidence: `0.96`
- Source: `skill:review_security.py`

```text
return os.system(user_input)
```

Recommendation: Use parameterized APIs or argument lists and validate untrusted input before the boundary.

## Warnings

- **[MEDIUM] Behavioral source changes have no focused test change**
  `commands.py` · `test_missing` · confidence `0.65`
  ```text
The patch changes source files but no test file.
```
  Recommendation: Add a focused regression test for the changed behavior.

## Needs Human Review

None.

## Checks Performed

- unified diff parsing
- six deterministic code\-review Skill rules

## Filter Decisions

- `allow` — `python3 scripts/run_review_rules.py work/inputs/security.diff`: command is read\-only and within the configured budget

## Sandbox Runs

- `simulated` — `python3 scripts/run_review_rules.py work/inputs/security.diff` (0.10 ms, exit=0)

## Monitoring

- Total duration: `50.00 ms`
- Sandbox duration: `0.10 ms`
- Tool calls: `1`
- Blocked executions: `0`
- Findings: `1`
- Severity distribution: `{'critical': 1}`
- Exception distribution: `{}`

## Conclusion

Deterministic review of 1 changed file\(s\).
