# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Unit tests for individual safety rules."""
from __future__ import annotations

from trpc_agent_sdk.safety import PolicyConfig
from trpc_agent_sdk.safety import RiskLevel
from trpc_agent_sdk.safety import ScanInput
from trpc_agent_sdk.safety._rules import DangerousFilesRule
from trpc_agent_sdk.safety._rules import DependencyInstallRule
from trpc_agent_sdk.safety._rules import NetworkRule
from trpc_agent_sdk.safety._rules import ProcessRule
from trpc_agent_sdk.safety._rules import ResourceAbuseRule
from trpc_agent_sdk.safety._rules import SecretLeakRule


def _policy():
    return PolicyConfig(whitelisted_domains=["api.github.com", "localhost"])


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
    rule = DangerousFilesRule()
    inp = ScanInput(
        script="with open('/home/u/.ssh/id_rsa') as f:\n    f.read()\n",
        language="python",
    )
    findings = rule.check(inp, _policy())
    assert findings, "should flag read of sensitive file"
    msg = findings[0].metadata.get("message", "")
    assert "Read sensitive file" in msg, msg
    assert "Write" not in msg, f"misclassified read as write: {msg}"
    assert "'w'" not in findings[0].evidence, findings[0].evidence


def test_dangerous_files_write_to_sensitive_flagged_as_write():
    rule = DangerousFilesRule()
    inp = ScanInput(script="open('/home/u/.ssh/id_rsa', 'w')", language="python")
    findings = rule.check(inp, _policy())
    assert findings
    msg = findings[0].metadata.get("message", "")
    assert "Write" in msg, msg


def test_network_python_non_allowlisted():
    rule = NetworkRule()
    inp = ScanInput(
        script="import requests\nrequests.get('https://evil.example.com')\n",
        language="python",
    )
    findings = rule.check(inp, _policy())
    assert findings and findings[0].risk_level == RiskLevel.HIGH


def test_network_python_allowlisted():
    rule = NetworkRule()
    inp = ScanInput(
        script="import requests\nrequests.get('https://api.github.com')\n",
        language="python",
    )
    assert rule.check(inp, _policy()) == []


def test_network_bash_curl_evil():
    rule = NetworkRule()
    inp = ScanInput(script="curl https://evil.example.com/x", language="bash")
    findings = rule.check(inp, _policy())
    assert findings and findings[0].risk_level == RiskLevel.HIGH


def test_process_subprocess_shell_true():
    rule = ProcessRule()
    inp = ScanInput(
        script="import subprocess\nsubprocess.run('ls', shell=True)\n",
        language="python",
    )
    findings = rule.check(inp, _policy())
    assert findings
    assert any(f.risk_level == RiskLevel.CRITICAL for f in findings)


def test_process_alias_os_system():
    rule = ProcessRule()
    inp = ScanInput(script="import os as x\nx.system('id')\n", language="python")
    findings = rule.check(inp, _policy())
    assert findings


def test_process_from_import_system():
    rule = ProcessRule()
    inp = ScanInput(script="from os import system\nsystem('id')\n", language="python")
    findings = rule.check(inp, _policy())
    assert findings


def test_process_bash_sudo():
    rule = ProcessRule()
    inp = ScanInput(script="sudo rm -rf /", language="bash")
    findings = rule.check(inp, _policy())
    assert any(f.risk_level == RiskLevel.CRITICAL for f in findings)


def test_process_base64_pipe():
    rule = ProcessRule()
    inp = ScanInput(script="echo xx | base64 -d | sh", language="bash")
    findings = rule.check(inp, _policy())
    assert findings


def test_dependency_pip_install():
    rule = DependencyInstallRule()
    inp = ScanInput(script="pip install evil-pkg", language="bash")
    findings = rule.check(inp, _policy())
    assert findings


def test_dependency_python_m_pip():
    rule = DependencyInstallRule()
    inp = ScanInput(script="python -m pip install foo", language="bash")
    findings = rule.check(inp, _policy())
    assert findings


def test_resource_infinite_loop():
    rule = ResourceAbuseRule()
    inp = ScanInput(script="while True:\n    pass\n", language="python")
    findings = rule.check(inp, _policy())
    assert findings


def test_resource_fork_bomb():
    rule = ResourceAbuseRule()
    inp = ScanInput(script=":(){ :|:& };:", language="bash")
    findings = rule.check(inp, _policy())
    assert findings
    assert findings[0].risk_level == RiskLevel.CRITICAL


def test_secret_hardcoded_key():
    rule = SecretLeakRule()
    inp = ScanInput(
        script="key = 'sk-abcdefghijklmnopqrstuvwxyz012345'\nprint(key)\n",
        language="python",
    )
    findings = rule.check(inp, _policy())
    assert findings


def test_secret_env_access():
    rule = SecretLeakRule()
    inp = ScanInput(
        script="import os\nprint(os.environ['OPENAI_API_KEY'])\n",
        language="python",
    )
    findings = rule.check(inp, _policy())
    assert findings


def test_secret_redacts_evidence():
    from trpc_agent_sdk.safety._rules import redact
    assert redact("sk-abcdefghijklmnopqrstuvwxyz").endswith("***")
