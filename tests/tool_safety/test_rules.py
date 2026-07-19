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


def test_dangerous_files_bash_rm_gnu_long_options():
    """Regression for CongkeChen review: rm --recursive / --force must not bypass R001.

    GNU rm supports --recursive/--force long options. The original _DELETE_PATTERNS
    only matched short options (-rf), so 'rm --recursive --force /' was missed.
    """
    rule = DangerousFilesRule()
    for cmd in (
        "rm --recursive /",
        "rm --force /",
        "rm --recursive --force /",
        "rm --force --recursive /",
        "rm --recursive -f /",
        "rm -r --force /",
        "rm --recursive=yes /",
    ):
        findings = rule.check(ScanInput(script=cmd, language="bash"), _policy())
        assert any("R001" in f.rule_id for f in findings), f"failed to flag: {cmd!r}"


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


def test_dangerous_files_python_remove_sensitive_path():
    """Regression for CongkeChen review: os.remove('/etc/passwd') must trigger
    R001 even without recursive delete. Previously only rmtree/unlink-with-r
    were flagged, so single-file delete of sensitive paths was silently
    allowed while the bash equivalent (rm /etc/passwd) was caught."""
    rule = DangerousFilesRule()
    inp = ScanInput(script="import os\nos.remove('/etc/passwd')\n", language="python")
    findings = rule.check(inp, _policy())
    assert findings and findings[0].rule_id == "R001_dangerous_files"


def test_dangerous_files_python_unlink_ssh_key():
    """os.unlink('~/.ssh/id_rsa') must trigger R001 (single-file sensitive delete)."""
    rule = DangerousFilesRule()
    inp = ScanInput(script="import os\nos.unlink('/home/u/.ssh/id_rsa')\n", language="python")
    findings = rule.check(inp, _policy())
    assert findings and findings[0].rule_id == "R001_dangerous_files"


def test_resource_dd_not_false_positive_on_adduser():
    """Regression for CongkeChen review: \\bdd\\b matched any 'dd' substring,
    so 'adduser', 'findd', 'xdd' were false-positive HIGH/DENY. The regex now
    requires dd as a command token (after start/space/;/&&/||)."""
    rule = ResourceAbuseRule()
    inp = ScanInput(script="adduser bob\nfind / -name xdd\n", language="bash")
    findings = rule.check(inp, _policy())
    # No dd finding should fire for adduser/findd/xdd.
    assert not any("dd can write" in f.metadata.get("message", "") for f in findings)


def test_resource_dd_real_command_still_caught():
    """Real 'dd if=/dev/zero of=/tmp/big bs=1M count=100' must still be caught."""
    rule = ResourceAbuseRule()
    inp = ScanInput(script="dd if=/dev/zero of=/tmp/big bs=1M count=100\n", language="bash")
    findings = rule.check(inp, _policy())
    assert any("dd can write" in f.metadata.get("message", "") for f in findings)


def test_dangerous_files_forbidden_path_boundary_not_substring():
    """Regression for CongkeChen review: _matches_forbidden used substring
    matching, so '.env' matched 'my.envrc'. Now uses path-boundary matching."""
    rule = DangerousFilesRule()
    policy = PolicyConfig(forbidden_paths=[".env"])
    # 'my.envrc' must NOT match forbidden path '.env' (no path boundary before .env).
    inp = ScanInput(script="open('my.envrc')\n", language="python")
    findings = rule.check(inp, policy)
    # No forbidden-path finding should fire for my.envrc.
    assert not any("forbidden" in f.metadata.get("message", "").lower() for f in findings)


def test_dangerous_files_system_dir_boundary_not_substring():
    """Regression for CongkeChen review: _matches_system_dir used substring
    matching, so '/etc' matched '/etcetera'. Now uses path-boundary matching."""
    rule = DangerousFilesRule()
    # '/etcetera/foo' must NOT match system dir '/etc' (no path boundary after /etc).
    inp = ScanInput(script="open('/etcetera/foo')\n", language="python")
    findings = rule.check(inp, _policy())
    # No system-dir finding should fire for /etcetera.
    assert not any("system" in f.metadata.get("message", "").lower() for f in findings)


def test_strict_command_allowlist_empty_list_is_fail_closed():
    """Regression for CongkeChen review: strict_command_allowlist=True with
    empty allowed_commands used to skip the whole allow-list check (fail-open),
    letting rm/chmod through. Now every non-builtin command is flagged HIGH."""
    from trpc_agent_sdk.safety._rules import ProcessRule
    # Use ProcessRule's _check_bash path (DangerousFilesRule also runs but
    # ProcessRule carries the allow-list enforcement).
    rule = ProcessRule()
    policy = PolicyConfig(strict_command_allowlist=True, allowed_commands=[])
    inp = ScanInput(script="rm /tmp/x\n", language="bash")
    findings = rule.check(inp, policy)
    allow_list_findings = [f for f in findings if "allow-list" in f.metadata.get("message", "")]
    assert allow_list_findings, "strict_command_allowlist=True with empty allowed_commands must flag rm (fail-closed)"
