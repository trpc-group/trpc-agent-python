# Code Review Report — `cr-sample-security`

## 1. Findings summary
- Active findings: **3**
- Warnings: 1
- Needs human review: 0

## 2. Severity statistics
- critical: 0 · high: 1 · medium: 1 · low: 1

## 3. Needs human review
- **[low] Source changed without accompanying tests** (`security.py`, missing_tests, conf=0.60, rule)
  - 1 source file(s) changed; no test file changed
  - _Fix:_ Add or update tests covering the changed code.

## 4. Filter interception summary
_none_

## 5. Monitoring metrics
- total_sec: 0.525
- sandbox_sec: 0
- tool_calls: 4
- block_count: 0
- finding_count: 3
- severity_dist: {'low': 1, 'high': 1, 'medium': 1}
- exception_dist: {}

## 6. Sandbox execution summary
_none_

## 7. Findings & fixes
- **[low] blacklist** (`security.py:1`, security, conf=0.90, static)
  - 1 import subprocess
2 
3 def run(cmd):
  - _Fix:_ Consider possible security implications associated with the subprocess module.
- **[high] subprocess_popen_with_shell_equals_true** (`security.py:4`, security, conf=0.90, static)
  - 3 def run(cmd):
4     return subprocess.call(cmd, shell=True)
5
  - _Fix:_ subprocess call with shell=True identified, security issue.
- **[medium] blacklist** (`security.py:7`, security, conf=0.90, static)
  - 6 def calc(expr):
7     return eval(expr)
  - _Fix:_ Use of possibly insecure function - consider using safer ast.literal_eval.
