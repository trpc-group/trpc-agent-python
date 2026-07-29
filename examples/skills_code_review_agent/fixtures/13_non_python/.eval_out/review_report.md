# Code Review Report

- task: `06c2cc11f7f1` | status: **succeeded** | mode: diff_only | runtime: local | dry_run: True
- input: fixture `13_non_python` | diff sha256/16: `e30188539f8410b4`

## Findings summary

| severity | count |
|---|---|
| critical | 1 |
| high | 0 |
| medium | 0 |
| low | 0 |
| info | 0 |

1 finding(s), 0 warning(s), 0 for human review, 0 duplicate(s) suppressed.

## Findings

### [CRITICAL] deploy/config.yaml:3 — Hardcoded Slack token
- rule: `SECRET005` | category: secrets | confidence: high | source: static
- evidence: `slack_webhook: xoxb…<redacted>`
- recommendation: Remove the credential from source, rotate/revoke it immediately, and load it at runtime from the environment or a secret manager.
- suggested fix:
  ```
  - slack_webhook: xoxb…<redacted>
  + slack_webhook: ${SLACK_WEBHOOK}
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
| `python3 scripts/run_checks.py` | local | ok | 0 | 95 | False |

## Metrics

- total: 1782 ms (sandbox: 802 ms)
- tool calls: 2 | filter blocks: 0
- token usage: {"prompt": 0, "completion": 0, "total": 0, "llm_calls": 3, "source": "event_stream(usage_metadata); zeros in dry-run"}
- error distribution: {}
- phase timings (ms): {"persist_input": 19, "sandbox_setup": 106, "agent_loop": 1633, "collect_findings": 0, "persist_findings": 23, "render_report": 0, "source_note": "tool_calls/sandbox_ms/errors/tokens from event stream; filter_blocks/findings/phases self-instrumented"}
