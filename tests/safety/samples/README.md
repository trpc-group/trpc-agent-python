# 12 Script Samples for Tool Script Safety Guard

These are the **publicly provided 12 script samples** required by Issue #90's
acceptance criterion #1.  Each file can be scanned individually:

```bash
python scripts/tool_safety_check.py tests/safety/samples/01_safe_python.py
python scripts/tool_safety_check.py tests/safety/samples/02_dangerous_deletion.py
python scripts/tool_safety_check.py tests/safety/samples/11_bash_pipe.sh --type bash
```

Or scan them all at once with the acceptance verifier:

```bash
python scripts/verify_acceptance.py
```

| # | File | Type | Expected Decision | Risk Category |
|---|------|------|-------------------|---------------|
| 1 | `01_safe_python.py` | python | allow | — |
| 2 | `02_dangerous_deletion.py` | python | deny | dangerous file ops |
| 3 | `03_credential_read.py` | python | deny | credential read |
| 4 | `04_network_egress.py` | python | deny | network egress |
| 5 | `05_whitelisted_network.py` | python | allow | — |
| 6 | `06_subprocess_call.py` | python | needs_human_review | process/system |
| 7 | `07_shell_injection.py` | python | deny | shell injection |
| 8 | `08_dependency_install.py` | python | deny | dependency install |
| 9 | `09_infinite_loop.py` | python | deny | resource abuse |
| 10 | `10_secret_leak.py` | python | deny | secret leak |
| 11 | `11_bash_pipe.sh` | bash | deny | bash pipe + network |
| 12 | `12_needs_review.py` | python | needs_human_review | process/system |
