# Code Review Report
- Task: `03ed666b0d2245f4a68fb83e4987c3a1`
- Conclusion: **blocked**
- Input: security_eval.diff (runtime=local, dry_run=True)
- Findings: 3 (by severity: {'high': 3}), needs human review: 0, deduplicated: 0, intercepts: 0

## Findings
| Severity | Category | File | Line | Title | Confidence | Source |
|---|---|---|---|---|---|---|
| high | security | app/handler.py | 11 | Use of eval() on dynamic data | 0.9 | static |
| high | security | app/handler.py | 12 | subprocess with shell=True | 0.85 | static |
| high | security | app/handler.py | 13 | SQL built with f-string (possible injection) | 0.85 | static |

### Recommendations
- `app/handler.py:11` Avoid eval(); use ast.literal_eval or explicit parsing.
- `app/handler.py:12` Pass an argument list and shell=False to avoid shell injection.
- `app/handler.py:13` Use parameterized queries (placeholders) instead of string interpolation.

## Needs Human Review
_none_

## Filter Intercepts
_none_

## Sandbox Runs
- `parse_diff.py`: ok (exit=0, 23ms, timed_out=False)
- `check_security.py`: ok (exit=0, 25ms, timed_out=False)
- `check_async_leak.py`: ok (exit=0, 26ms, timed_out=False)
- `check_db_lifecycle.py`: ok (exit=0, 23ms, timed_out=False)
- `check_tests_missing.py`: ok (exit=0, 21ms, timed_out=False)
- `check_secrets.py`: ok (exit=0, 27ms, timed_out=False)

## Metrics
```json
{
  "total_duration_ms": 245,
  "sandbox_duration_ms": 145,
  "tool_calls": 7,
  "intercepts": 0,
  "findings_total": 3,
  "severity_distribution": {
    "high": 3
  },
  "error_distribution": {}
}
```

## LLM Summary
Dry-run review complete. Static findings are authoritative.

## Warnings
- local runtime is a development fallback; use container or cube in production
