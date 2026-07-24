# Code Review Report: sample-security-review

Input: `fixture:security_issue`

## Summary

- Conclusion: High-risk issues found; block merge until fixes are applied.
- Findings: 2
- Warnings: 0
- Needs human review: 1
- Severity distribution: `{'high': 2, 'low': 1}`

## Findings

### HIGH security: subprocess uses shell=True

- Location: `app/search.py:5`
- Evidence: `return subprocess.check_output(cmd, shell=True)`
- Recommendation: Pass an argument list with shell=False and validate any user-controlled arguments.
- Confidence: `0.88`
- Source: `rule:shell-injection`

### HIGH security: Dynamic code execution introduced

- Location: `app/search.py:8`
- Evidence: `return eval(expr)`
- Recommendation: Avoid eval/exec on runtime data; use a constrained parser or explicit dispatch table.
- Confidence: `0.9`
- Source: `rule:dangerous-exec`


## Warnings

No warnings.

## Needs Human Review

### LOW testing: Production code changed without tests

- Location: `app/search.py:4`
- Evidence: `app/search.py changed, but no test file was included in the diff.`
- Recommendation: Add or update tests that cover the changed behavior before merging.
- Confidence: `0.62`
- Source: `rule:test-coverage`


## Filter Intercepts

- `needs_human_review` `script.high_risk_command`: command contains network, package installation, privilege or destructive operations

## Monitoring

- total_duration_ms: `231`
- sandbox_duration_ms: `143`
- tool_call_count: `3`
- intercept_count: `1`
- finding_count: `2`
- warning_count: `0`
- needs_human_review_count: `1`
- severity_distribution: `{'high': 2, 'low': 1}`
- exception_type_distribution: `{'FilterIntercept': 1}`
- redaction_count: `0`
- changed_file_count: `1`
- changed_line_count: `5`

## Sandbox Runs

- `parse-diff` runtime=`dry-run-local` status=`succeeded` duration_ms=`73` timed_out=`False`
- `static-rules` runtime=`dry-run-local` status=`succeeded` duration_ms=`70` timed_out=`False`
- `high-risk-script-probe` runtime=`dry-run-local` status=`filtered` duration_ms=`0` timed_out=`False`

## Fix Recommendations

- app/search.py:5 - Pass an argument list with shell=False and validate any user-controlled arguments.
- app/search.py:8 - Avoid eval/exec on runtime data; use a constrained parser or explicit dispatch table.
- app/search.py:4 - Add or update tests that cover the changed behavior before merging.
