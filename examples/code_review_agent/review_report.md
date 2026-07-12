# Code Review Dry-run Report

**Mode:** `dry_run`
**Status:** `completed`
**Task ID:** `review-7bb30cf34bf8`

## Summary

Found 1 finding(s) across 1 changed file(s).

## Final conclusion

Review completed with high-confidence findings.

## Metrics

- Files reviewed: 1
- Hunks reviewed: 1
- Changed lines reviewed: 3
- Findings: 1
- Warnings / needs human review: 0
- Duration ms: 0
- Sandbox duration ms: 0
- Sandbox runs: 2
- Tool calls: 2
- Filter intercepts: 0
- Redactions: 1

## Severity distribution

- high: 1

## Category distribution

- secrets: 1

## Findings

### 1. Hard-coded secret in changed code

- Severity: `high`
- Category: `secrets`
- Location: `src/config.py:2`
- Confidence: `high`
- Source: `fake_model`
- Fingerprint: `9b95e3b4d91e69ea`

Evidence: Added line contains a secret-like assignment: API_KEY = "<REDACTED_SECRET>"

Recommendation: Move the secret to an environment variable or secret manager, remove it from source control, and rotate the exposed value.


## Warnings / needs human review

No warnings.

## Filter governance summary

- `post` `changed_line_anchor` -> `allow` src/config.py:2: Finding is anchored to an added changed line.
- `post` `redaction` -> `redact` src/config.py:2: Redacted secret-like content from finding fields.
- `pre_sandbox` `sandbox_governance` -> `allow` [diff_summary]: Sandbox request passed pre-execution governance.
- `pre_sandbox` `sandbox_governance` -> `allow` [static_rules]: Sandbox request passed pre-execution governance.

## Sandbox execution summary

- `diff_summary` on `fake`: exit 0, 0 ms, truncated=false
- `static_rules` on `fake`: exit 0, 0 ms, truncated=false

## Audit / exceptions

No exceptions recorded.

## Actionable recommendations

- `src/config.py:2` Move the secret to an environment variable or secret manager, remove it from source control, and rotate the exposed value.
