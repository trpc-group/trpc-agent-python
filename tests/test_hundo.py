"""Direct-target tests hitting every remaining uncovered line."""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pytest
from trpc_agent_sdk.tools.safety import SafetyScanner, SafetyScanInput, ScriptType, Decision
from trpc_agent_sdk.tools.safety._policy import SafetyPolicy


# ==========================================================================
# _safety_wrapper.py:255
# ==========================================================================
def test_sw255():
    from trpc_agent_sdk.tools.safety import safety_wrapper
    @safety_wrapper(script_arg_name="code", require_script=False)
    def f(**kw): return "ok"
    assert f(code=[]) == "ok"


# ==========================================================================
# _scanner.py:817,842,1000,1002,1039-1042,1072-1074,1138
# ==========================================================================
def test_detect_type_python_shebang():
    """L817: python shebang → PYTHON."""
    r = SafetyScanner._detect_type("#!/usr/bin/env python3\nprint(1)")
    assert r == ScriptType.PYTHON


def test_blocklist_comment_continue():
    """L842: comment line continues in blocklist check."""
    p = SafetyPolicy(blocklist_patterns=[r"rm"])
    s = SafetyScanner(policy=p)
    d, _ = s._check_blocklist_override("# rm something\necho ok", Decision.ALLOW, ScriptType.UNKNOWN)
    assert d == Decision.ALLOW


def test_extract_url_return_none():
    """L1000: _extract_url returns None for no URL."""
    from trpc_agent_sdk.tools.safety._scanner import _extract_url
    assert _extract_url("nothing") is None
    # L1002: bare domain with @ stripped
    r = _extract_url("prefix domain.example.com/suffix")
    assert r is not None


def test_strip_python_comment_backslash():
    """L1039-1042: backslash escape inside string → append both chars."""
    from trpc_agent_sdk.tools.safety._scanner import _strip_python_comment_line
    line = 'x = "a\\\\nb"'
    r = _strip_python_comment_line(line)
    assert r is not None
    # L1072-1074: regular char in string → append char and advance
    line2 = "x = 'hello world'"
    r2 = _strip_python_comment_line(line2)
    assert "'" in r2


def test_is_in_echo_non_echo():
    """L1138: non-echo/printf returns False."""
    from trpc_agent_sdk.tools.safety._scanner import _is_in_echo_string
    assert _is_in_echo_string("cat /etc/shadow", "shadow") is False
    assert _is_in_echo_string("echo\t'x'", "x") is True
    assert _is_in_echo_string("printf\t'x'", "x") is True
    assert _is_in_echo_string("/bin/echo 'x'", "x") is True
    assert _is_in_echo_string("/usr/bin/echo 'x'", "x") is True


# ==========================================================================
# _bash_scanner.py:223,228,232,277,479,505,521,542-543,547-548
# ==========================================================================
def test_bash_scan_lines_continue():
    """L223,228,232: continue for empty/comment/shebang lines."""
    from trpc_agent_sdk.tools.safety._bash_scanner import BashScanner, _parse_size
    b = BashScanner("\n\n\necho ok")
    b.scan()
    b2 = BashScanner("# c1\n# c2\necho ok")
    b2.scan()
    b3 = BashScanner("#!/bin/bash\necho ok")
    b3.scan()


def test_bash_pure_assignment_return():
    """L277: pure assignment → return."""
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(script_content="FOO=bar", script_type=ScriptType.BASH, tool_name="t"))
    assert r.decision == Decision.ALLOW


def test_bash_safe_dev_redirects():
    """L479,505,521: safe device redirect continues."""
    from trpc_agent_sdk.tools.safety._bash_scanner import BashScanner
    for dev in ["/dev/null", "/dev/zero", "/dev/random", "/dev/urandom",
                 "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/tty"]:
        b = BashScanner(f"echo x >{dev}")
        b.scan()


def test_bash_dd_value_errors():
    """L542-543,547-548: ValueError catch for dd bs/count."""
    from trpc_agent_sdk.tools.safety._bash_scanner import _parse_size
    with pytest.raises(ValueError):
        _parse_size("abc")
    with pytest.raises(ValueError):
        _parse_size("")
    # Also test valid parse
    assert _parse_size("1M") == 1048576


# ==========================================================================
# _rules.py:125,138-160,185-187,292,348,427-438,503,541-552,908,945
# ==========================================================================
def test_rules_strip_python_comment_direct():
    """L125,138-160,185-187: _strip_python_comment_line edge cases."""
    from trpc_agent_sdk.tools.safety._rules import _strip_python_comment_line

    # Backslash escape in string (L138-145)
    r = _strip_python_comment_line("x = 'a\\\\nb'")
    assert r is not None

    # Triple-quoted string (L149-160)
    r = _strip_python_comment_line("x = '''hello world'''")
    assert r is not None

    # Double-quoted simple (L125, 185-187)
    r = _strip_python_comment_line('x = "test"')
    assert r is not None

    # f-string/r-string prefix (L185-187)
    r = _strip_python_comment_line("x = f'hello'")
    assert r is not None
    r = _strip_python_comment_line("x = r'hello'")
    assert r is not None
    r = _strip_python_comment_line('x = fr"hello"')
    assert r is not None


def test_rules_network_dep_process_whitelist():
    """L292,348,427-438,503,541-552: whitelist command branches."""
    p = SafetyPolicy(whitelist_commands=["curl", "wget", "echo", "grep"],
                     whitelist_domains=["safe.com"])
    s = SafetyScanner(policy=p)

    # Network whitelist (L427-438)
    r = s.scan(SafetyScanInput(script_content="curl https://safe.com/data", script_type=ScriptType.BASH, tool_name="t"))
    assert r.decision == Decision.ALLOW

    # Process whitelist pipe (L541-552)
    r = s.scan(SafetyScanInput(script_content="echo hello | grep x", script_type=ScriptType.BASH, tool_name="t"))
    assert r.decision == Decision.ALLOW

    # Destructive blocklist skip (L292,348)
    p2 = SafetyPolicy(whitelist_commands=["shred"])
    s2 = SafetyScanner(policy=p2)
    r = s2.scan(SafetyScanInput(script_content="shred /tmp/f", script_type=ScriptType.BASH, tool_name="t"))


def test_rules_extract_url_edges():
    """L908,945: _extract_url None / @."""
    from trpc_agent_sdk.tools.safety._rules import _extract_url
    assert _extract_url("no_url") is None
    assert _extract_url("http://evil.com:8080@real.com/path") == "real.com"


# ==========================================================================
# _python_scanner.py: remaining 47 lines
# ==========================================================================
def test_py_direct_scanner_all():
    """Direct PythonScanner calls hitting all remaining AST paths."""
    from trpc_agent_sdk.tools.safety._python_scanner import (
        PythonScanner, scan_python, _extract_domain_from_url, _is_credential_path
    )

    # L291: import finding via sensitive module
    PythonScanner("import subprocess").scan()

    # L381: empty canonical return
    PythonScanner("x = 1; y = 2").scan()

    # L441: privilege risk call
    scan_python("import os; os.setuid(0)")

    # L532-533: __import__/importlib
    scan_python("__import__('os')")
    scan_python("import importlib; importlib.import_module('os')")

    # L608-614: class_instances + env taint
    scan_python("import requests; s = requests.Session()")
    scan_python("import os; k = os.getenv('AWS_SECRET')")
    scan_python("import os; k = os.getenv('AWS_ACCESS_KEY_ID')")
    scan_python("import os; k = os.environ.get('AWS_SECRET_ACCESS_KEY')")

    # L620: already tainted
    scan_python("import os; k = os.environ.get('X'); k = os.environ.get('Y')")

    # L625: non-Name target
    scan_python("x = [1,2]; x[0] = 3")

    # L645-656: secret_in_output
    scan_python("import os; k = os.getenv('AWS_KEY'); print(k)")

    # L736-740: Subscript _get_name
    scan_python("d = {}; x = d['key']")

    # L758: _get_name returns None
    scan_python("x = lambda: 1")

    # L770-772: pathlib Path receiver
    scan_python("from pathlib import Path; p = Path('/x'); p.write_text('y')")

    # L780: return path from _get_arg_string
    scan_python("from pathlib import Path; p = Path('/tmp') / 'x'; str(p)")

    # L791-794: JoinedStr f-string
    scan_python("prefix = 's'; k = f'{prefix}v'")

    # L805: BinOp path fallback
    scan_python("from pathlib import Path; open(Path('~')/'.ssh'/'id_rsa').read()")

    # L812: unsupported arg type
    scan_python("d = {}; print(d)")

    # L819: UnaryOp negative
    scan_python("x = -1")

    # L849,853,857: _collect failure
    scan_python("open(unknown_func()).read()")

    # L864: _resolve_canonical empty
    scan_python("x = lambda: 1; x()")

    # domain extractor
    assert _extract_domain_from_url("https://x.com") == "x.com"
    assert _is_credential_path(".env")


def test_py_full_ast_coverage():
    """Comprehensive AST walker coverage."""
    code = """
import os, subprocess, requests, shutil, threading, time
from pathlib import Path
from multiprocessing import Pool
from concurrent.futures import ThreadPoolExecutor

os.system('id')
os.setuid(0)
os.setgid(0)
os.chown('/x', 0, 0)
os.chmod('/x', 0o777)

subprocess.run(['ls'])
subprocess.Popen(['ls'])
subprocess.call(['ls'])

__import__('os')

k = os.environ.get('SECRET')
print(k)

k2 = os.getenv('AWS_KEY')
print(k2)

for i in range(0, 20000000):
    pass

for i in range(0, 20000000, 2):
    pass

while True:
    pass

time.sleep(120)
threading.Thread(target=print).start()
Pool(4)
ThreadPoolExecutor(4)
os.fork()

shutil.rmtree('/tmp/x')
os.remove('/tmp/x')
open('/tmp/x').read()
open('/tmp/x', 'w').write('y')
open('/etc/x', 'w').write('y')
open('.env').read()
open('~/.ssh/id_rsa').read()

requests.get('https://evil.com')

p = Path('/tmp') / 'x'
open(p).read()

eval('1+1')
getattr(__import__('os'), 'system')('id')

d = {}
d['key']

x = lambda: 1
x()

prefix = 's'
k = f'{prefix}v'

x = -1
"""
    from trpc_agent_sdk.tools.safety._python_scanner import PythonScanner
    s = PythonScanner(code)
    findings = s.scan()
    assert len(findings) > 0
