# Code Review Report

- Task: `example-security`
- Status: **completed**
- Dry run: **no**
- Input: 1 changed file(s), 1 added line(s)
- Rule set digest: `daf2588b52f9ba2a84239d97305de003d2d36082a0eefb0e2c0049447674c889`
- Conclusion: Found 2 actionable issue(s).

## Severity summary

- high: 1
- low: 1

## Findings

### [HIGH] Unsafe shell execution

`app.py:1` · security · confidence 0.94

subprocess.run(user_input, shell=True)

Recommendation: Pass a fixed argument vector and keep shell disabled.

### [LOW] No related test change

`app.py:1` · testing · confidence 0.68

subprocess.run(user_input, shell=True)

Recommendation: Add or update a focused test for this behavior.

## Human review

- None

## Execution and policy

- Sandbox checks: 1
- Blocked decisions: 0
- `custom_rule`: completed, exit=0, duration=125ms

## Monitoring

- Total duration: 262ms
- Tool calls: 1
- Filter blocks: 0
- Errors: {}
