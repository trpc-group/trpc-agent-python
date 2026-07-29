# Code Review Report

- task: `612dab832a7d` | status: **succeeded** | mode: diff_only | runtime: local | dry_run: True
- input: fixture `08_secrets` | diff sha256/16: `0abf0b1d6bf86cf9`

## Findings summary

| severity | count |
|---|---|
| critical | 3 |
| high | 1 |
| medium | 0 |
| low | 0 |
| info | 0 |

4 finding(s), 0 warning(s), 0 for human review, 0 duplicate(s) suppressed.

## Findings

### [CRITICAL] config.py:3 — Hardcoded AWS access key ID
- rule: `SECRET001` | category: secrets | confidence: high | source: static
- evidence: `AWS_ACCESS_KEY_ID = "AKIA…<redacted>"`
- recommendation: Deactivate and rotate the key in IAM immediately, audit CloudTrail for misuse, then read it from the environment or a secret manager.
- suggested fix:
  ```
  - AWS_ACCESS_KEY_ID = "AKIA…<redacted>"
  + AWS_ACCESS_KEY_ID = os.environ["AWS_ACCESS_KEY_ID"]
  ```

### [CRITICAL] config.py:4 — Hardcoded GitHub token
- rule: `SECRET003` | category: secrets | confidence: high | source: static
- evidence: `GITHUB_TOKEN = "ghp_…<redacted>"`
- recommendation: Revoke the token in GitHub settings, rotate dependent automation, and inject it via CI/environment secrets.
- suggested fix:
  ```
  - GITHUB_TOKEN = "ghp_…<redacted>"
  + GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
  ```

### [CRITICAL] config.py:5 — Credentials embedded in URL
- rule: `SECRET011` | category: secrets | confidence: high | source: static
- evidence: `DATABASE_URL = "postgresql://svc_admin:***@db.internal:5432/orders"`
- recommendation: Strip the password out of the URL (it leaks into logs, shell history and error messages), rotate it, and splice it in from the environment at runtime.
- suggested fix:
  ```
  - DATABASE_URL = "postgresql://svc_admin:***@db.internal:5432/orders"
  + DATABASE_URL = "postgresql://svc_admin:${DB_PASSWORD}@db.internal:5432/orders"
  ```

### [HIGH] settings.yaml:3 — Sensitive variable assigned hardcoded literal
- rule: `SECRET012` | category: secrets | confidence: medium | source: static
- evidence: `api_key: "hC9w…<redacted>" [regex]`
- recommendation: Move the value out of source control: read it from the environment or a secret manager and rotate the exposed value.
- suggested fix:
  ```
  - api_key: "hC9w…<redacted>"
  + api_key: ${API_KEY}
  ```

## Needs human review

(none)

## Warnings (low confidence)

(none)

## Filter decisions

2 decision(s), 0 blocked.

| tool | decision | rule | reason |
|---|---|---|---|
| skill_load | allow | skill_allowlist | ok |
| skill_run | allow | policy | ok |

## Sandbox executions

| command | runtime | status | exit | duration_ms | timed_out |
|---|---|---|---|---|---|
| `python3 scripts/run_checks.py` | local | ok | 0 | 97 | False |

## Metrics

- total: 1158 ms (sandbox: 556 ms)
- tool calls: 2 | filter blocks: 0
- token usage: {"prompt": 0, "completion": 0, "total": 0, "llm_calls": 3, "source": "event_stream(usage_metadata); zeros in dry-run"}
- error distribution: {}
- phase timings (ms): {"persist_input": 21, "sandbox_setup": 62, "agent_loop": 1049, "collect_findings": 2, "persist_findings": 23, "render_report": 0, "source_note": "tool_calls/sandbox_ms/errors/tokens from event stream; filter_blocks/findings/phases self-instrumented"}
