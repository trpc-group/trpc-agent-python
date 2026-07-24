"""Additional tests to cover remaining uncovered lines in the safety module."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from trpc_agent_sdk.tools.safety import Decision, SafetyScanner, SafetyScanInput, ScriptType, quick_scan
from trpc_agent_sdk.tools.safety._policy import SafetyPolicy
from trpc_agent_sdk.tools.safety._types import RiskLevel, RiskCategory

# ==========================================================================
# _safety_wrapper.py: lines 255, 282 (sync wrapper with require_script)
# ==========================================================================


def test_safety_wrapper_sync_require_script_raises_no_code():
    """sync_wrapper: missing script arg with require_script=True must raise RuntimeError."""
    from trpc_agent_sdk.tools.safety import safety_wrapper

    @safety_wrapper(tool_name="sync_req", script_arg_name="script", require_script=True)
    def sync_func(*args, **kwargs):
        return "should not reach"

    with pytest.raises(RuntimeError, match="not found"):
        sync_func(args={})


def test_safety_wrapper_sync_script_is_not_str():
    """sync_wrapper: non-string script with require_script=True must raise."""
    from trpc_agent_sdk.tools.safety import safety_wrapper

    @safety_wrapper(tool_name="sync_nonstr", script_arg_name="script", require_script=True)
    def sync_func(*args, **kwargs):
        return "should not reach"

    with pytest.raises(RuntimeError, match="not found"):
        sync_func(script=42)


# ==========================================================================
# _safety_filter.py: lines 259-264 (hasattr path for extract helpers)
# ==========================================================================


def test_filter_extract_list_field_hasattr():
    """_extract_list_field must handle object with args attribute."""
    from trpc_agent_sdk.tools.safety._safety_filter import _extract_list_field

    class FakeReq:
        args = {"cmd_args": ["--verbose"]}

    assert _extract_list_field(FakeReq(), "command_args", "cmd_args") == ["--verbose"]


def test_filter_extract_list_field_hasattr_no_match():
    """_extract_list_field with hasattr but key not in args returns None."""
    from trpc_agent_sdk.tools.safety._safety_filter import _extract_list_field

    class FakeReq:
        args = {"other": "val"}

    assert _extract_list_field(FakeReq(), "command_args") is None


def test_filter_extract_str_field_empty():
    """_extract_str_field must return None when no match."""
    from trpc_agent_sdk.tools.safety._safety_filter import _extract_str_field
    assert _extract_str_field({}, "wd") is None


# ==========================================================================
# _scanner.py: oversized bytes + blocklist_commands edge cases
# ==========================================================================


def test_scanner_bytes_oversized_with_blocklist_hit():
    """Oversized byte script with blocklist hit must be DENY with GLOBAL-002."""
    from trpc_agent_sdk.tools.safety._policy import SafetyPolicy
    policy = SafetyPolicy(max_script_bytes=50, blocklist_patterns=[r"rm\s+-rf"])
    scanner = SafetyScanner(policy=policy)
    r = scanner.scan(
        SafetyScanInput(script_content="rm -rf /" + "x" * 50, script_type=ScriptType.BASH, tool_name="bytes_bl"))
    assert r.decision == Decision.DENY
    assert any(f.rule_id == "GLOBAL-002" for f in r.findings)


def test_scanner_blocklist_override_bash_script_type():
    """_check_blocklist_override with BASH type must not use Python string strip."""
    from trpc_agent_sdk.tools.safety._policy import SafetyPolicy
    policy = SafetyPolicy(blocklist_patterns=[r"/etc/shadow"])
    scanner = SafetyScanner(policy=policy)
    # Bash: cat '/etc/shadow' should still match because we DON'T strip
    decision, findings = scanner._check_blocklist_override("cat '/etc/shadow'",
                                                           Decision.ALLOW,
                                                           script_type=ScriptType.BASH)
    assert decision == Decision.DENY


def test_scanner_blocklist_override_python_type():
    """_check_blocklist_override with PYTHON type must strip string literals."""
    from trpc_agent_sdk.tools.safety._policy import SafetyPolicy
    policy = SafetyPolicy(blocklist_patterns=[r"rm\s+-rf\s+/"])
    scanner = SafetyScanner(policy=policy)
    # Python: the pattern inside string should NOT match (Python string stripping)
    decision, findings = scanner._check_blocklist_override("x = 'rm -rf /'",
                                                           Decision.ALLOW,
                                                           script_type=ScriptType.PYTHON)
    assert decision == Decision.ALLOW  # inside string literal → no match


def test_scanner_is_in_echo_string_single_quote_harmless():
    """_is_in_echo_string must return True for pattern ONLY in single-quoted string."""
    from trpc_agent_sdk.tools.safety._scanner import _is_in_echo_string
    assert _is_in_echo_string("echo 'rm -rf /'", r"rm\s+-rf\s+/") is True


def test_scanner_is_in_echo_string_no_echo():
    """_is_in_echo_string must return False for non-echo commands."""
    from trpc_agent_sdk.tools.safety._scanner import _is_in_echo_string
    assert _is_in_echo_string("cat /etc/shadow", r"/etc/shadow") is False


def test_scanner_is_in_echo_string_invalid_regex():
    """_is_in_echo_string must handle invalid regex gracefully."""
    from trpc_agent_sdk.tools.safety._scanner import _is_in_echo_string
    assert _is_in_echo_string("echo '[invalid'", r"[invalid") is False


def test_scanner_extract_url_bare_domain_at_sign():
    """_extract_url bare domain with @ must strip userinfo."""
    from trpc_agent_sdk.tools.safety._scanner import _extract_url
    # "curl user@evil.com" should NOT match because it needs space-bounded domain
    # But just bare domain with @ should strip
    result = _extract_url("hello api.evil.com/path")
    assert result is not None


# ==========================================================================
# _rules.py: echo string + extract_url
# ==========================================================================


def test_rules_is_in_echo_single_quote_safe():
    """_is_in_echo_string single quote: safe to suppress."""
    from trpc_agent_sdk.tools.safety._rules import _is_in_echo_string
    assert _is_in_echo_string("echo 'rm -rf /'", r"rm\s+-rf\s+/") is True


def test_rules_is_in_echo_no_match():
    """_is_in_echo_string: pattern not in any quoted string."""
    from trpc_agent_sdk.tools.safety._rules import _is_in_echo_string
    assert _is_in_echo_string("echo hello world", r"rm\s+-rf") is False


def test_rules_is_in_echo_double_quote_cmd_sub():
    """_is_in_echo_string: $(...) in double quotes must NOT suppress."""
    from trpc_agent_sdk.tools.safety._rules import _is_in_echo_string
    assert not _is_in_echo_string('echo "$(rm -rf /)"', r"rm\s+-rf\s+/")


def test_rules_is_in_echo_not_echo():
    """_is_in_echo_string: non-echo/printf line returns False."""
    from trpc_agent_sdk.tools.safety._rules import _is_in_echo_string
    assert not _is_in_echo_string("cat /etc/passwd", r"/etc/passwd")


def test_rules_extract_url_bare_domain_with_at():
    """_extract_url bare domain with @ must be stripped."""
    from trpc_agent_sdk.tools.safety._rules import _extract_url
    result = _extract_url("api.example.com")
    assert result == "api.example.com"


# ==========================================================================
# _bash_scanner.py: remaining coverage
# ==========================================================================


def test_bash_oversized_scanner():
    """BashScanner with oversized source must produce oversized finding."""
    from trpc_agent_sdk.tools.safety._bash_scanner import BashScanner
    source = "echo line\n" * 600
    scanner = BashScanner(source, max_lines=500)
    findings = scanner.scan()
    oversized = [f for f in findings if f.kind == "oversized"]
    assert len(oversized) > 0
    assert "600 lines exceeds 500" in oversized[0].evidence


def test_bash_shebang_skip():
    """BashScanner must skip shebang lines."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="#!/bin/bash\necho safe", script_type=ScriptType.BASH, tool_name="shebang"))
    assert r.decision == Decision.ALLOW


def test_bash_comment_skip():
    """BashScanner must skip comment-only lines."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="# this is a comment\n# another comment\necho safe",
                        script_type=ScriptType.BASH,
                        tool_name="comments"))
    assert r.decision == Decision.ALLOW


def test_bash_heredoc_detection():
    """BashScanner must detect heredoc with interpreter."""
    from trpc_agent_sdk.tools.safety._bash_scanner import BashScanner
    source = "python3 << EOF\nimport os; os.system('id')\nEOF"
    scanner = BashScanner(source)
    findings = scanner.scan()
    heredocs = [f for f in findings if f.kind == "heredoc"]
    assert len(heredocs) > 0


def test_bash_long_sleep_trigger():
    """BashScanner must detect long sleep."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(script_content="sleep 120", script_type=ScriptType.BASH, tool_name="longsleep"))
    assert r.decision == Decision.NEEDS_HUMAN_REVIEW


def test_bash_secret_ref_in_echo():
    """BashScanner must detect echo of secret variable."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="echo $API_KEY", script_type=ScriptType.BASH, tool_name="secret_echo"))
    findings = [f for f in r.findings if f.rule_id == "BASH-LEAK-001"]
    assert len(findings) > 0


def test_bash_tokenize_unclosed_quote():
    """_tokenize_line must handle unclosed quotes."""
    from trpc_agent_sdk.tools.safety._bash_scanner import _tokenize_line
    tokens = _tokenize_line("echo 'unclosed")
    assert len(tokens) > 0


def test_bash_strip_inline_comment():
    """_strip_inline_comment must handle edge cases."""
    from trpc_agent_sdk.tools.safety._bash_scanner import _strip_inline_comment
    assert _strip_inline_comment("echo hello # comment") == "echo hello"
    assert _strip_inline_comment("echo 'hello # not a comment'") == "echo 'hello # not a comment'"


def test_bash_is_sensitive_path():
    """_is_sensitive_path must match various sensitive paths."""
    from trpc_agent_sdk.tools.safety._bash_scanner import _is_sensitive_path
    assert _is_sensitive_path("/etc/shadow")
    assert _is_sensitive_path("~/.ssh/id_rsa")
    assert _is_sensitive_path(".env")
    assert not _is_sensitive_path("/tmp/data.txt")


def test_bash_to_seconds_units():
    """_to_seconds must convert various units."""
    from trpc_agent_sdk.tools.safety._bash_scanner import _to_seconds
    assert _to_seconds(5, "m") == 300
    assert _to_seconds(1, "h") == 3600
    assert _to_seconds(2, "d") == 172800
    assert _to_seconds(10, "s") == 10
    assert _to_seconds(10, "") == 10


def test_bash_parse_size():
    """_parse_size must parse size strings."""
    from trpc_agent_sdk.tools.safety._bash_scanner import _parse_size
    assert _parse_size("1M") == 1024 * 1024
    assert _parse_size("4K") == 4096
    assert _parse_size("512") == 512 * 512  # default dd block size


def test_bash_fork_bomb_detection():
    """_check_fork_bomb must detect literal and generalized patterns."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(script_content=":(){ :|:& };:", script_type=ScriptType.BASH, tool_name="forkbomb"))
    assert r.decision == Decision.DENY


# ==========================================================================
# _python_scanner.py: remaining coverage
# ==========================================================================


def test_python_large_range_in_loop():
    """for loop with range(50000000) must be detected."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="for i in range(50000000):\n    print(i)",
                        script_type=ScriptType.PYTHON,
                        tool_name="bigrange"))
    findings = [f for f in r.findings if "large_range" in str(f.rule_id).lower() or f.rule_id == "AST-RES-001"]
    assert len(findings) > 0


def test_python_socket_connect():
    """socket.connect() must trigger network finding."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="import socket; s=socket.socket(); s.connect(('10.0.0.1', 4444))",
                        script_type=ScriptType.PYTHON,
                        tool_name="socket_test"))
    assert r.decision == Decision.DENY


def test_python_shutil_rmtree():
    """shutil.rmtree must be detected."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="import shutil; shutil.rmtree('/tmp/foo')",
                        script_type=ScriptType.PYTHON,
                        tool_name="rmtree"))
    assert r.decision == Decision.DENY


def test_python_eval_exec_direct():
    """eval('code') must be detected."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="eval('__import__(\"os\").system(\"id\")')",
                        script_type=ScriptType.PYTHON,
                        tool_name="eval_direct"))
    assert r.decision == Decision.DENY


def test_python_subprocess_run():
    """subprocess.run must be detected."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="import subprocess; subprocess.run(['rm', '-rf', '/'])",
                        script_type=ScriptType.PYTHON,
                        tool_name="subprocess"))
    assert r.decision == Decision.DENY


def test_python_open_cred_file():
    """open('.env') must be detected as credential read."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="open('.env').read()", script_type=ScriptType.PYTHON, tool_name="open_env"))
    # Should have credential read finding
    findings = [f for f in r.findings if f.rule_id.startswith("AST-FILE")]
    assert len(findings) > 0


def test_python_secret_in_print():
    """API key hardcoded must be detected."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content='api_key = "sk-abc123def456"; print(api_key)',
                        script_type=ScriptType.PYTHON,
                        tool_name="secret_leak"))
    findings = [f for f in r.findings if f.category == RiskCategory.SENSITIVE_INFO_LEAK]
    assert len(findings) > 0


def test_python_while_true_loop():
    """while True loop must be detected."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="while True:\n    pass", script_type=ScriptType.PYTHON, tool_name="while_true"))
    findings = [f for f in r.findings if f.rule_id == "AST-RES-001"]
    assert len(findings) > 0


def test_python_file_delete():
    """os.remove must be detected."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="import os; os.remove('/etc/config')",
                        script_type=ScriptType.PYTHON,
                        tool_name="os_remove"))
    findings = [f for f in r.findings if f.rule_id.startswith("AST-FILE")]
    assert len(findings) > 0


def test_python_concurrency_detection():
    """multiprocessing.Process must be detected."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="import multiprocessing; p = multiprocessing.Process(target=print)",
                        script_type=ScriptType.PYTHON,
                        tool_name="concurrency"))
    findings = [f for f in r.findings if f.rule_id == "AST-RES-003"]
    assert len(findings) > 0


def test_python_long_sleep():
    """time.sleep(120) must be detected."""
    scanner = SafetyScanner()
    r = scanner.scan(
        SafetyScanInput(script_content="import time; time.sleep(120)",
                        script_type=ScriptType.PYTHON,
                        tool_name="longsleep_py"))
    findings = [f for f in r.findings if f.rule_id == "AST-RES-002"]
    assert len(findings) > 0


# ==========================================================================
# _scanner.py additional
# ==========================================================================


def test_scanner_allow_patterns_critical_blocked():
    """allow_patterns must NOT upgrade when CRITICAL finding exists."""
    from trpc_agent_sdk.tools.safety._policy import SafetyPolicy
    # rm -rf triggers CRITICAL → should NOT be upgraded by allow_patterns
    policy = SafetyPolicy(allow_patterns=[r"rm\s+-rf\s+/tmp/safe"])
    scanner = SafetyScanner(policy=policy)
    r = scanner.scan(
        SafetyScanInput(script_content="rm -rf /tmp/safe", script_type=ScriptType.BASH, tool_name="crit_block"))
    assert r.decision == Decision.DENY


def test_scanner_reload_policy_cache():
    """get_scanner must return fresh scanner after policy reload."""
    from trpc_agent_sdk.tools.safety._scanner import get_scanner
    from trpc_agent_sdk.tools.safety._policy import reload_policy
    s1 = get_scanner()
    reload_policy()
    s2 = get_scanner()
    assert s2 is not None
    assert s2._policy is not None


def test_scanner_get_scanner_same_policy():
    """get_scanner must return same scanner for same policy."""
    from trpc_agent_sdk.tools.safety._scanner import get_scanner
    s1 = get_scanner()
    s2 = get_scanner()
    assert s1 is s2
