# Code Review Report

- Task ID: `cr_39fc2e5b8c38`
- Status: `completed`
- Conclusion: Block merge until high-severity findings are fixed.
- Finding schema version: 1
- Confidence thresholds: `{'finding': 0.8, 'warning': 0.55}`
- Sandbox policy: `{'runtime': 'fake', 'timeout_sec': 5.0, 'max_output_bytes': 12000, 'filter_timeout_budget_sec': 30.0, 'filter_max_output_bytes': 20000, 'env_whitelist': ['CR_ALLOW_TEST_COMMAND', 'CR_REPO_PATH', 'CR_TEST_COMMAND', 'CR_TEST_TIMEOUT', 'LANG', 'LC_ALL', 'PATH', 'PYTHONPATH'], 'network_policy': 'deny'}`
- Filter policy: `{'network_policy': 'deny', 'timeout_budget_sec': 30.0, 'max_output_bytes': 20000, 'allowed_network_domains': ['semgrep.dev', 'registry.semgrep.dev'], 'schema_version': 1, 'forbidden_path_markers': ['.env', '.ssh/', 'id_rsa', 'private_key', '.aws/', '/etc/', 'secrets/'], 'high_risk_command_patterns': ['curl\\s+[^|]+\\|\\s*(sh|bash)', 'wget\\s+[^|]+\\|\\s*(sh|bash)', 'rm\\s+-rf\\s+/', 'docker\\s+run\\s+.*--privileged', '\\bsudo\\b', '^\\s*(sh|bash|zsh|fish|dash|ksh)\\b', '[;&|`<>]', '\\$\\(', ':\\(\\)\\s*\\{'], 'sandbox_path_allowlist': ['scripts/', 'work/'], 'sandbox_read_allowlist': ['scripts/', 'work/', 'repo/'], 'sandbox_write_allowlist': ['work/']}`
- Files: 1
- Findings: 3
- Warnings: 1
- Needs human review: 0

## Severity Summary

- `high`: 3
- `medium`: 1

## Findings

- `high` `security` `f297d314bad9db23` app/deploy.py:4 - Shell command execution uses shell=True
  Evidence: `subprocess.run("git checkout " + branch, shell=True)`
  Recommendation: Pass command arguments as a list and avoid shell=True.
  Confidence: 0.92; Source: `rule:command-injection`
  Hunk: `@@ -1,5 +1,8 @@`
  Context before: `['import subprocess', '', 'def deploy(branch):']`
  Context after: `['    config = eval(open("deploy.json").read())', '    requests.get("https://internal.example", verify=False)', '    return True', '']`
- `high` `security` `390bae33637bbee5` app/deploy.py:6 - TLS certificate verification disabled
  Evidence: `requests.get("https://internal.example", verify=False)`
  Recommendation: Keep certificate verification enabled and configure trusted CA roots.
  Confidence: 0.90; Source: `rule:tls-verify`
  Hunk: `@@ -1,5 +1,8 @@`
  Context before: `['def deploy(branch):', '    subprocess.run("git checkout " + branch, shell=True)', '    config = eval(open("deploy.json").read())']`
  Context after: `['    return True', '']`
- `high` `security` `ab76d82de3b40f6e` app/deploy.py:5 - Dynamic code execution introduced
  Evidence: `config = eval(open("deploy.json").read())`
  Recommendation: Replace dynamic execution with a constrained parser or explicit dispatch table.
  Confidence: 0.88; Source: `rule:dynamic-code`
  Hunk: `@@ -1,5 +1,8 @@`
  Context before: `['', 'def deploy(branch):', '    subprocess.run("git checkout " + branch, shell=True)']`
  Context after: `['    requests.get("https://internal.example", verify=False)', '    return True', '']`

## Warnings

- `medium` `missing_tests` `b83ef1a622841df9` app/deploy.py:4 - Source change has no matching test update
  Evidence: `Changed source files without a tests/ or test_*.py diff in the same review input.`
  Recommendation: Add or update a focused regression test.
  Confidence: 0.72; Source: `rule:missing-tests`

## Needs Human Review

No manual-review items.

## Filter Decisions

- `allow` `sandbox-preflight` scripts/diff_summary.py: Sandbox request passed path, read/write allowlist, command, network, timeout, and output-budget checks.
- `allow` `sandbox-preflight` scripts/static_review.py: Sandbox request passed path, read/write allowlist, command, network, timeout, and output-budget checks.
- `allow` `sandbox-preflight` scripts/test_probe.py: Sandbox request passed path, read/write allowlist, command, network, timeout, and output-budget checks.
- `allow` `sandbox-preflight` scripts/scanner_probe.py: Sandbox request passed path, read/write allowlist, command, network, timeout, and output-budget checks.

## Filter Interception Summary

- Decision distribution: `{'allow': 4}`
- Interceptions: 0
- No deny or manual-review interceptions.

## Skill Audit

- Skill: `code-review`
- Scripts loaded: 5
- Rule manifest: `rules.json` sha256=75fd43814187b4d1
- Rule doc: `docs/rules.md` sha256=28b3ab7c7e5e1cc1
- Rule doc: `docs/sandbox_policy.md` sha256=e089768c0cae6cbf
- Script: `scripts/diff_summary.py` sha256=7382022593d1b7d5
- Script: `scripts/scanner_probe.py` sha256=ae2fca1349d2887f
- Script: `scripts/static_review.py` sha256=9c723637e417b34b
- Script: `scripts/test_probe.py` sha256=377f536d986465fd
- Script: `scripts/unit_test_probe.py` sha256=d4646765faa27dd8

## Sandbox Summary

- `diff_summary` runtime=`fake` status=`passed` exit=0 duration_ms=0
- `static_review` runtime=`fake` status=`passed` exit=0 duration_ms=0
- `test_probe` runtime=`fake` status=`passed` exit=0 duration_ms=0
- `scanner_probe` runtime=`fake` status=`passed` exit=0 duration_ms=0

## Monitoring

- Total duration ms: 20
- Sandbox duration ms: 0
- Stage durations ms: `{'parse': 4, 'storage_create_task': 0, 'filter': 1, 'storage_filter_decisions': 1, 'sandbox': 0, 'storage_sandbox_runs': 0, 'rules': 1, 'storage_findings': 1, 'report': 0}`
- Risk level: `high`
- Tool calls: 4
- Filter decisions: 4
- Filter interceptions: 0
- Filter decision distribution: `{'allow': 4}`
- Redactions: 0
- Deduped findings: 0
- Ignored findings: 0
- Exception distribution: `{}`
