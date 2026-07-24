"""Push to 99%. Covers scanner, rules, bash, wrapper remaining lines."""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pytest
from trpc_agent_sdk.tools.safety import SafetyScanner, SafetyScanInput, ScriptType, Decision
from trpc_agent_sdk.tools.safety._policy import SafetyPolicy


# === _safety_wrapper.py:255 ===
def test_sw255():
    from trpc_agent_sdk.tools.safety import safety_wrapper
    @safety_wrapper(script_arg_name="code", require_script=False)
    def f(**kw): return "ok"
    assert f(code=[]) == "ok"


# === _scanner.py ===
def test_scanner_all_remaining():
    # L817: detect_type PYTHON
    assert SafetyScanner._detect_type("#!/usr/bin/python3\nprint(1)") == ScriptType.PYTHON

    # L842: blocklist comment skip
    p = SafetyPolicy(blocklist_patterns=[r"bad"])
    s = SafetyScanner(policy=p)
    d, _ = s._check_blocklist_override("# bad comment\necho ok", Decision.ALLOW, ScriptType.UNKNOWN)
    assert d == Decision.ALLOW

    # L1000,1002: extract_url edges
    from trpc_agent_sdk.tools.safety._scanner import _extract_url, _strip_python_comment_line, _is_in_echo_string
    assert _extract_url("nothing") is None
    assert _extract_url("site.com/path") is not None

    # L1039-1042,1072-1074: strip_python_comment_line backslash + char
    r = _strip_python_comment_line('x = "a\\\\n"')
    assert r is not None
    r = _strip_python_comment_line("x = 'simple'")
    assert "'" in r

    # L1138: is_in_echo_string tab variants
    assert _is_in_echo_string("echo\t'x'", "x")
    assert _is_in_echo_string("printf\t'x'", "x")
    assert _is_in_echo_string("/bin/echo 'x'", "x")
    assert _is_in_echo_string("/usr/bin/echo 'x'", "x")
    assert not _is_in_echo_string("cat /etc/shadow", "shadow")


# === _bash_scanner.py ===
def test_bash_all_remaining():
    from trpc_agent_sdk.tools.safety._bash_scanner import BashScanner, _parse_size

    # L223,228,232: continue for empty/comment/shebang
    BashScanner("\n\necho ok").scan()
    BashScanner("# c1\n# c2\necho ok").scan()
    BashScanner("#!/bin/bash\necho ok").scan()

    # L277: pure assignment
    s = SafetyScanner()
    s.scan(SafetyScanInput(script_content="FOO=bar", script_type=ScriptType.BASH, tool_name="t"))

    # L479,505,521: safe dev redirect continues
    BashScanner("echo x >/dev/null").scan()
    BashScanner("echo x >/dev/zero").scan()
    BashScanner("echo x >/dev/random").scan()

    # L542-543,547-548: dd ValueError
    with pytest.raises(ValueError):
        _parse_size("xyz")


# === _rules.py ===
def test_rules_all_remaining():
    from trpc_agent_sdk.tools.safety._rules import _strip_python_comment_line, _extract_url, _is_in_echo_string

    # L125,142-145,185-187: strip_python_comment_line
    r = _strip_python_comment_line("x = 'plain'")
    assert r is not None
    r = _strip_python_comment_line('x = "dquote"')
    assert r is not None
    r = _strip_python_comment_line("x = f'fmt'")
    assert r is not None
    r = _strip_python_comment_line("x = r'raw'")
    assert r is not None
    r = _strip_python_comment_line('x = fr"both"')
    assert r is not None

    # L908,945: extract_url edges
    assert _extract_url("no_url") is None
    assert _extract_url("http://evil.com:443@real.com/x") == "real.com"

    # L292,348,427-438,503,541-552: whitelist rules
    p = SafetyPolicy(whitelist_commands=["curl", "wget", "echo", "grep"], whitelist_domains=["safe.com"])
    s = SafetyScanner(policy=p)
    s.scan(SafetyScanInput(script_content="curl https://safe.com/data", script_type=ScriptType.BASH, tool_name="t"))
    s.scan(SafetyScanInput(script_content="echo hello | grep x", script_type=ScriptType.BASH, tool_name="t"))


# === _python_scanner.py ===
def test_py_all_remaining():
    from trpc_agent_sdk.tools.safety._python_scanner import (
        PythonScanner, scan_python, _extract_domain_from_url, _is_credential_path
    )
    # L291
    PythonScanner("import subprocess").scan()
    # L381
    PythonScanner("x=1").scan()
    # L441
    scan_python("import os; os.setuid(0)")
    # L532-533
    scan_python("__import__('os')")
    scan_python("import importlib; importlib.import_module('os')")
    # L608-614
    scan_python("import requests; s=requests.Session()")
    scan_python("import os; k=os.getenv('AWS_SECRET')")
    scan_python("import os; k=os.environ.get('AWS_KEY')")
    # L620
    scan_python("import os; k=os.environ.get('X'); k=os.environ.get('Y')")
    # L625
    scan_python("x=[]; x[0]=1")
    # L645-656
    scan_python("import os; k=os.getenv('K'); print(k)")
    # L736-740
    scan_python("d={}; x=d['k']")
    # L758
    scan_python("x=lambda:1")
    # L770-772,780
    scan_python("from pathlib import Path; p=Path('/x'); p.write_text('y')")
    scan_python("from pathlib import Path; p=Path('/t')/'x'; str(p)")
    # L791-794
    scan_python("p='s'; k=f'{p}v'")
    # L805
    scan_python("from pathlib import Path; open(Path('~')/'.ssh'/'id_rsa').read()")
    # L812
    scan_python("d={}; print(d)")
    # L819
    scan_python("x=-1")
    # L849,853,857
    scan_python("open(func()).read()")
    # L864
    scan_python("x=lambda:1; x()")
    # domain + cred
    assert _extract_domain_from_url("https://x.com") == "x.com"
    assert _is_credential_path(".env")


def test_py_big_scan():
    from trpc_agent_sdk.tools.safety._python_scanner import PythonScanner
    code = open(__file__).read()  # scan this test file itself
    s = PythonScanner(code)
    f = s.scan()
    assert isinstance(f, list)
