# Code Review Report (Task: task_sample)

**Status**: COMPLETED
**Total Duration**: 433 ms
**Sandbox execution duration**: 364 ms

## Findings Summary
- Critical: 0
- High: 2
- Medium: 1
- Low: 0

## High Confidence Findings
### 1. [MEDIUM] Missing unit test file or test updates
- **Category**: Missing Test
- **File**: src/utils.py:1
- **Evidence**: `Modified source file: src/utils.py without any matching test file changes in diff`
- **Recommendation**: Create or update a test file (e.g., tests/test_utils.py) to verify these changes.

### 2. [HIGH] Subprocess execution with shell=True
- **Category**: Security Risk
- **File**: src/utils.py:4
- **Evidence**: `subprocess.run(f"echo {user_input}", shell=True)`
- **Recommendation**: Avoid using shell=True to prevent command/shell injection risks. Pass arguments as a list instead.

### 3. [HIGH] Unsafe deserialization using pickle
- **Category**: Security Risk
- **File**: src/utils.py:5
- **Evidence**: `data = pickle.loads(user_input)`
- **Recommendation**: Use json or safer deserialization methods instead of pickle to prevent arbitrary code execution.

## Sandbox Execution Details
- **Command**: `python skills/code-review/scripts/parse_diff.py --diff examples/skills_code_review_agent/fixtures/fixture_security.diff --output parsed_task_sample.json` (SUCCESS in 162 ms)
- **Command**: `python skills/code-review/scripts/run_checks.py --parsed-diff parsed_task_sample.json --output findings_task_sample.json` (SUCCESS in 202 ms)
