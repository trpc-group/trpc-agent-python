# Code Review Output Schema

Each code review produces structured findings in JSON format.

## Finding Fields

| Field | Type | Description |
|-------|------|-------------|
| `severity` | string | One of: `critical`, `high`, `medium`, `low`, `info` |
| `category` | string | One of: `security`, `async_error`, `resource_leak`, `db_lifecycle`, `missing_tests`, `secret_info` |
| `file` | string | Relative file path where the issue was found |
| `line` | int | Line number (1-indexed) |
| `title` | string | Short human-readable finding title |
| `evidence` | string | Code snippet or diff fragment showing the issue |
| `recommendation` | string | Actionable fix suggestion |
| `confidence` | float | 0.0–1.0 confidence score |
| `source` | string | Scanner or rule that produced this finding |

## Confidence Tiers

| Tier | Threshold | Meaning |
|------|-----------|---------|
| High-confidence | >= 0.8 | Automated finding, reliable |
| Warning | >= 0.55 | Likely issue, needs attention |
| Needs human review | < 0.55 | Possible false positive, reviewer discretion |

## Report Structure

```json
{
  "task_id": "review-20260709-123456-abc12345",
  "generated_at": "2026-07-09T12:00:00Z",
  "summary": {
    "total_findings": 5,
    "high_confidence": 3,
    "needs_human_review": 2,
    "by_severity": {"critical": 1, "high": 1, "medium": 2, "low": 1, "info": 0}
  },
  "filter_summary": {},
  "sandbox_summary": {},
  "telemetry": {},
  "findings": [...],
  "human_review_items": [...],
  "recommendations": [...]
}
```
