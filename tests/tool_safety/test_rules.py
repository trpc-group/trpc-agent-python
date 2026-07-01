# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Unit tests for individual safety rules."""
from __future__ import annotations

from examples.tool_safety.safety import PolicyConfig
from examples.tool_safety.safety import RiskLevel
from examples.tool_safety.safety import ScanInput
from examples.tool_safety.safety.rules import DangerousFilesRule
from examples.tool_safety.safety.rules import DependencyInstallRule
from examples.tool_safety.safety.rules import NetworkRule
from examples.tool_safety.safety.rules import ProcessRule
from examples.tool_safety.safety.rules import ResourceAbuseRule
from examples.tool_safety.safety.rules import SecretLeakRule


def _policy():
    return PolicyConfig(whitelisted_domains=["api.github.com", "localhost"])


# ----- dangerous files -----


def test_dangerous_files_python_rmtree():
    rule = DangerousFilesRule()
    inp = ScanInput(script="import shutil\nshutil.rmtree('/etc')\n", language="python")
    findings = rule.check(inp, _policy())
    assert findings and findings[0].rule_id == "R001_dangerous_files"
    assert findings[0].risk_level == RiskLevel.CRITICAL


def test_dangerous_files_bash_rm_rf():
    rule = DangerousFilesRule()
    inp = ScanInput(script="rm -rf /home/user", language="bash")
    findings = rule.check(inp, _policy())
    assert any("R001" in f.rule_id for f in findings)


def test_dangerous_files_read_ssh_key():
    rule = DangerousFilesRule()
    inp = ScanInput(script="open('/home/u/.ssh/id_rsa')", language="python")
    findings = rule.check(inp, _policy())
    assert findings and findings[0].risk_level == RiskLevel.CRITICAL


def test_dangerous_files_read_only_not_misclassified_as_write():
    """Regression: open('/x/.ssh/id_rsa') is read-only. Evidence must NOT
    claim write mode ('w'). Filename contains 'a' which previously caused
    _is_write_open to misclassify it."""
    rule = DangerousFilesRule()
    inp = ScanInput(script="with open('/home/u/.ssh/id_rsa') as f:\n    f.read()\n", language="python")
    findings = rule.check(inp, _policy())
    assert findings, "should flag read of sensitive file"
    # Must be flagged as READ, not WRITE.
    msg = findings[0].metadata.get("message", "")
    assert "Read sensitive file" in msg, msg
    assert "Write" not in msg, f"misclassified read as write: {msg}"
    assert "'w'" not in findings[0].evidence, findings[0].evidence


def test_dangerous_files_write_to_sensitive_flagged_as_write():
    """open('/x/.ssh/id_rsa', 'w') is a write — must be flagged as write."""
    rule = DangerousFilesRule()
    inp = ScanInput(script="open('/home/u/.ssh/id_rsa', 'w')", language="python")
    findings = rule.check(inp, _policy())
    assert findings
    msg = findings[0].metadata.get("message", "")
    assert "Write" in msg, msg


# ----- network -----


def test_network_python_non_allowlisted():
    rule = NetworkRule()
    inp = ScanInput(script="import requests\nrequests.get('https://evil.example.com')\n", language="python")
    findings = rule.check(inp, _policy())
    assert findings and findings[0].risk_level == RiskLevel.HIGH


def test_network_python_allowlisted():
    rule = NetworkRule()
    inp = ScanInput(script="import requests\nrequests.get('https://api.github.com')\n", language="python")
    assert rule.check(inp, _policy()) == []


def test_network_bash_curl_evil():
    rule = NetworkRule()
    inp = ScanInput(script="curl https://evil.example.com/x", language="bash")
    findings = rule.check(inp, _policy())
    assert findings and findings[0].risk_level == RiskLevel.HIGH


# ----- process -----


def test_process_subprocess_shell_true():
    rule = ProcessRule()
    inp = ScanInput(script="import subprocess\nsubprocess.run('ls', shell=True)\n", language="python")
    findings = rule.check(inp, _policy())
    assert findings and findings[0].risk_level == RiskLevel.CRITICAL


def test_process_eval():
    rule = ProcessRule()
    inp = ScanInput(script="eval('1+1')", language="python")
    findings = rule.check(inp, _policy())
    assert any(f.risk_level == RiskLevel.CRITICAL for f in findings)


def test_process_sudo():
    rule = ProcessRule()
    inp = ScanInput(script="sudo cat /etc/shadow", language="bash")
    findings = rule.check(inp, _policy())
    assert any(f.risk_level == RiskLevel.CRITICAL for f in findings)


# ----- dependency install -----


def test_dependency_pip_install():
    rule = DependencyInstallRule()
    inp = ScanInput(script="pip install malware", language="bash")
    findings = rule.check(inp, _policy())
    assert findings and findings[0].rule_id == "R004_dependency_install"


def test_dependency_embedded_in_python_string():
    rule = DependencyInstallRule()
    inp = ScanInput(script="import os\nos.system('npm install evil')\n", language="python")
    findings = rule.check(inp, _policy())
    assert any(f.rule_id == "R004_dependency_install" for f in findings)


# ----- resource abuse -----


def test_resource_infinite_loop():
    rule = ResourceAbuseRule()
    inp = ScanInput(script="while True:\n    print('x')\n", language="python")
    findings = rule.check(inp, _policy())
    assert findings and findings[0].rule_id == "R005_resource_abuse"


def test_resource_fork_bomb():
    rule = ResourceAbuseRule()
    inp = ScanInput(script=":(){ :|: & };:", language="bash")
    findings = rule.check(inp, _policy())
    assert any(f.risk_level == RiskLevel.CRITICAL for f in findings)


def test_resource_long_sleep():
    rule = ResourceAbuseRule()
    inp = ScanInput(script="import time\ntime.sleep(99999)\n", language="python")
    findings = rule.check(inp, _policy())
    assert findings


# ----- secret leak -----


def test_secret_hardcoded_key():
    rule = SecretLeakRule()
    inp = ScanInput(script='API_KEY = "sk-1234567890abcdef1234567890"\n', language="python")
    findings = rule.check(inp, _policy())
    assert findings and findings[0].rule_id == "R006_secret_leak"


def test_secret_logged_variable():
    rule = SecretLeakRule()
    inp = ScanInput(script="import logging\ntoken = 'x'\nlogging.info(token)\n", language="python")
    findings = rule.check(inp, _policy())
    assert any(f.rule_id == "R006_secret_leak" for f in findings)


def test_secret_bash_assignment():
    rule = SecretLeakRule()
    inp = ScanInput(script='API_KEY="sk-1234567890abcdef1234567890"', language="bash")
    findings = rule.check(inp, _policy())
    assert findings


def test_secret_evidence_redacted():
    rule = SecretLeakRule()
    inp = ScanInput(script='KEY = "sk-1234567890abcdef1234567890"\n', language="python")
    findings = rule.check(inp, _policy())
    assert findings
    # Evidence must not contain the full secret.
    assert "1234567890abcdef1234567890" not in findings[0].evidence
