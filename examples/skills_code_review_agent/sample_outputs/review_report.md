# Review Report

- Task ID: `9118ea7d-760d-4cf8-a383-7d14f298197e`
- Final Verdict: `fail`

## Summary

Loaded review input, parsed diff, completed deterministic rule review, and processed sandbox governance. Changed files: 2, added lines: 6, deleted lines: 1, findings: 2, human review: 0, warnings: 0, filter decisions: 3, sandbox runs: 3.

## Severity Stats

- `high`: 2

## Findings

- [`high`] `security` at `src/calculator.py:11`: Use of eval introduces code execution risk
  Evidence: `+    return eval(expression)`
  Recommendation: Replace eval with explicit parsing, a whitelist-based dispatcher, or a safe literal parser.
- [`high`] `security` at `src/calculator.py:14`: subprocess call enables shell execution
  Evidence: `+    return subprocess.run(command, shell=True, check=True, text=True)`
  Recommendation: Pass an argument list and avoid shell=True unless a reviewed shell command is unavoidable.

## Human Review Items

- None.

## Warnings

- None.

## Filter Summary

- `allow` on `skill:code-review/scripts/parse_diff.py`: Invocation allowed by default policy.
- `allow` on `skill:code-review/scripts/run_linters.py`: Invocation allowed by default policy.
- `allow` on `skill:code-review/scripts/run_tests.py`: Invocation allowed by default policy.

## Sandbox Summary

- `parse_diff` status=`succeeded` duration=188ms exit_code=0
- `run_linters` status=`succeeded` duration=182ms exit_code=0
- `run_tests` status=`succeeded` duration=218ms exit_code=0

## Monitoring

- `added_lines_count`: 6
- `category_distribution`: {'security': 2}
- `changed_files_count`: 2
- `deleted_lines_count`: 1
- `exception_distribution`: {}
- `filter_decision_count`: 3
- `finding_count`: 2
- `needs_human_review_count`: 0
- `sandbox_run_count`: 3
- `severity_distribution`: {'high': 2}
- `total_duration_ms`: 604
- `warning_count`: 0

## Actionable Recommendations

- Replace eval with explicit parsing, a whitelist-based dispatcher, or a safe literal parser.
- Pass an argument list and avoid shell=True unless a reviewed shell command is unavoidable.
