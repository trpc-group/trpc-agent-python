# Code Review Report

- Task: `review-sample-secret`
- Conclusion: High risk findings require changes before merge.
- Findings: 2
- Warnings: 1
- Needs human review: 0

## Metrics

- Total duration: 21 ms
- Sandbox duration: 1 ms
- Tool calls: 1
- Interceptions: 0
- Severity distribution: `{"critical": 2, "low": 1}`
- Category distribution: `{"secret": 2, "test": 1}`

## Findings

- **Hard-coded secret in changed code** `critical` `secret`
  - Location: `config.py:3`
  - Evidence: API_KEY = [REDACTED]
  - Recommendation: Move secrets to a managed secret store or environment configuration.
- **Hard-coded secret in changed code** `critical` `secret`
  - Location: `config.py:4`
  - Evidence: PASSWORD = [REDACTED]
  - Recommendation: Move secrets to a managed secret store or environment configuration.

## Warnings

- **Source change has no matching test update** `low` `test`
  - Location: `config.py:3`
  - Evidence: 1 of 1 source file(s) changed without a matching test or fixture change.
  - Recommendation: Add or update focused tests or fixtures for the changed behavior.

## Needs Human Review

None.

## Filter Events

- `allow` `unknown`: execution request allowed by example policy

## Sandbox Runs

- `dry-run` `success` exit=0 duration=1ms

## Fix Suggestions

- Address critical and high findings before merging.
- Add focused tests for changed source behavior.
- Review any governance or sandbox items before trusting execution output.
