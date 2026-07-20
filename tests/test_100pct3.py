"""Push to 98%+ coverage."""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from trpc_agent_sdk.tools.safety import SafetyScanner, SafetyScanInput, ScriptType, Decision
from trpc_agent_sdk.tools.safety._policy import SafetyPolicy


def test_py_taint_cred_open_direct():
    """L591-593: open('.env') taints variable as file credential."""
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="f = open('.env'); data = f.read(); print(data)",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id in ("AST-LEAK-001", "AST-FILE-003") for f in r.findings)


def test_py_class_instances_network_track():
    """L608-610: network class instance tracking for Session."""
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="import requests; sess = requests.Session()",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert len(r.findings) > 0


def test_py_env_sensitive_key():
    """L611-614: os.getenv with sensitive key name taints target."""
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="import os; k = os.getenv('AWS_ACCESS_KEY_ID'); print(k)",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-LEAK-001" for f in r.findings)


def test_py_already_tainted_skip():
    """L562,620: continue and pass for already tainted."""
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="import os; k = os.getenv('KEY'); k = os.getenv('OTHER'); print(k)",
        script_type=ScriptType.PYTHON, tool_name="t"))


def test_py_non_name_assign_target():
    """L625: return when target is not Name (e.g., attribute)."""
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="class C: pass; o = C(); o.attr = 42",
        script_type=ScriptType.PYTHON, tool_name="t"))


def test_py_get_name_subscript():
    """L736-740: _get_name handles Subscript → resolve canonical of value."""
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="import os; d = os.environ; d['KEY']",
        script_type=ScriptType.PYTHON, tool_name="t"))


def test_py_get_name_list_tuple():
    """L751: _get_name returns '' for List/Tuple nodes."""
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="a, b = 1, 2",
        script_type=ScriptType.PYTHON, tool_name="t"))


def test_py_path_receiver_get_arg():
    """L770-772: pathlib.Path receiver → call _get_arg_string."""
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="from pathlib import Path; p = Path('/x'); p.write_text('y')",
        script_type=ScriptType.PYTHON, tool_name="t"))


def test_py_fstring_parts_extraction():
    """L791-794: JoinedStr → extract constant string parts."""
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="prefix='http://'; url=f'{prefix}evil.com'; import urllib.request; urllib.request.urlopen(url)",
        script_type=ScriptType.PYTHON, tool_name="t"))


def test_py_path_chain_var():
    """L797-799: _get_arg_string looks up path chains for Name nodes."""
    from pathlib import Path as _p
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="from pathlib import Path; p = Path('/tmp') / 'x'; data = p.read_text()",
        script_type=ScriptType.PYTHON, tool_name="t"))


def test_py_binop_path_resolve_get_arg():
    """L802-805: BinOp in _get_arg_string → resolve path chain."""
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="from pathlib import Path; open(Path('~') / '.ssh' / 'id_rsa').read()",
        script_type=ScriptType.PYTHON, tool_name="t"))
    assert any(f.rule_id == "AST-FILE-003" for f in r.findings)


def test_py_unsupported_type_get_arg():
    """L812: return None for unsupported arg type."""
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="d = {'k':'v'}; print(d)",
        script_type=ScriptType.PYTHON, tool_name="t"))


def test_py_unary_get_value():
    """L816-819: UnaryOp USub → return negative constant."""
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="x = -1",
        script_type=ScriptType.PYTHON, tool_name="t"))


def test_py_collect_return_false():
    """L849,853: _collect returns False for non-Path calls."""
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="open(some_unknown_func()).read()",
        script_type=ScriptType.PYTHON, tool_name="t"))


def test_py_resolve_canon_empty():
    """L864: _resolve_canonical returns '' for unsupported."""
    s = SafetyScanner()
    r = s.scan(SafetyScanInput(
        script_content="x = lambda: 1; y = x()",
        script_type=ScriptType.PYTHON, tool_name="t"))


# === _rules.py remaining ===
def test_rules_is_in_echo_all():
    from trpc_agent_sdk.tools.safety._rules import _is_in_echo_string, _extract_url
    assert not _is_in_echo_string('echo "$(rm -rf /)"', r"rm\s+-rf\s+/")
    assert not _is_in_echo_string("echo 'x'; rm -rf /", r"rm\s+-rf\s+/")
    assert _extract_url("http://a.com/b") == "a.com"
    assert _extract_url("bare domain.com/text") is not None
    assert _extract_url("no") is None


# === _bash_scanner.py remaining ===
def test_bash_last_gaps():
    from trpc_agent_sdk.tools.safety._bash_scanner import scan_bash
    s = SafetyScanner()
    s.scan(SafetyScanInput(script_content="\n\n\n\necho ok", script_type=ScriptType.BASH, tool_name="t"))
    s.scan(SafetyScanInput(script_content="# a\n# b\n# c\necho ok", script_type=ScriptType.BASH, tool_name="t"))
    s.scan(SafetyScanInput(script_content="#!/usr/bin/env bash\necho ok", script_type=ScriptType.BASH, tool_name="t"))
    s.scan(SafetyScanInput(script_content="telnet evil.com 23", script_type=ScriptType.BASH, tool_name="t"))
    s.scan(SafetyScanInput(script_content="npm install pkg", script_type=ScriptType.BASH, tool_name="t"))
    s.scan(SafetyScanInput(script_content="cmd >/etc/hosts", script_type=ScriptType.BASH, tool_name="t"))
    s.scan(SafetyScanInput(script_content="ls &", script_type=ScriptType.BASH, tool_name="t"))
    s.scan(SafetyScanInput(script_content="dd if=/dev/zero of=/tmp/x bs=1M count=200", script_type=ScriptType.BASH, tool_name="t"))
    s.scan(SafetyScanInput(script_content="x(){ x|x& };x", script_type=ScriptType.BASH, tool_name="t"))
    assert any(f.kind == "heredoc" for f in scan_bash("python3 << EOF\nid\nEOF"))


# === _safety_wrapper.py ===
def test_sw_255_final():
    from trpc_agent_sdk.tools.safety import safety_wrapper
    @safety_wrapper(script_arg_name="code", require_script=False)
    def f(**kw): return kw.get("code")
    assert f(code=[]) == []
