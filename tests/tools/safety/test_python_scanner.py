# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the syntax-aware (L2) Python AST scanner."""

from __future__ import annotations

from trpc_agent_sdk.tools.safety import RiskCategory
from trpc_agent_sdk.tools.safety import default_policy
from trpc_agent_sdk.tools.safety import scan_python


def _ids(script: str) -> set[str]:
    """Return the set of rule ids the AST layer produces for ``script``."""
    return {hit.rule_id for hit in scan_python(script, default_policy())}


def test_plain_subprocess_call_detected() -> None:
    """A direct subprocess call is flagged as a process spawn."""
    assert "AST001" in _ids("import subprocess\nsubprocess.run(['ls'])\n")


def test_subprocess_import_alias_tracked() -> None:
    """``import subprocess as sp`` is resolved back to the real call.

    This is the anti-obfuscation core: a pure regex on ``subprocess.`` would
    miss the aliased ``sp.Popen`` call.
    """
    script = "import subprocess as sp\nsp.Popen('whoami', shell=True)\n"
    ids = _ids(script)
    assert "AST001" in ids


def test_from_import_alias_tracked() -> None:
    """``from os import system as run`` resolves ``run(...)`` to ``os.system``."""
    script = "from os import system as run\nrun('echo hi')\n"
    assert "AST001" in _ids(script)


def test_eval_exec_detected() -> None:
    """Dynamic code execution via eval/exec is flagged."""
    assert "AST002" in _ids("eval(user_input)\n")
    assert "AST002" in _ids("exec(compile(src, '<s>', 'exec'))\n")


def test_forbidden_path_open_detected_as_critical() -> None:
    """Opening an SSH private key is a critical sensitive-path access."""
    hits = scan_python("open('/home/me/.ssh/id_rsa').read()\n", default_policy())
    ast003 = [h for h in hits if h.rule_id == "AST003"]
    assert ast003
    assert ast003[0].category is RiskCategory.SENSITIVE_INFO_LEAK
    assert ast003[0].risk_level.value == "critical"


def test_getattr_obfuscation_detected() -> None:
    """A computed getattr name is flagged as obfuscation."""
    assert "AST007" in _ids("getattr(os, 'sys' + 'tem')('id')\n")


def test_infinite_loop_without_break_detected() -> None:
    """``while True`` with no break is a resource-abuse hit."""
    assert "AST005" in _ids("while True:\n    pass\n")


def test_infinite_loop_with_break_not_flagged() -> None:
    """A ``while True`` that can terminate is not flagged."""
    script = "while True:\n    if done():\n        break\n"
    assert "AST005" not in _ids(script)


def test_network_client_detected() -> None:
    """A requests/urllib call is flagged as network egress."""
    assert "AST004" in _ids("import requests\nrequests.get('http://x')\n")


def test_dynamic_command_needs_review() -> None:
    """A subprocess call built from a variable also raises the review-level hit."""
    script = "import subprocess\ncmd = build()\nsubprocess.run(cmd)\n"
    assert "AST008" in _ids(script)


def test_literal_os_system_is_not_flagged_dynamic() -> None:
    """``os.system`` with a string literal is a spawn (AST001) but not dynamic."""
    ids = _ids('os.system("ls -la")\n')
    assert "AST001" in ids
    assert "AST008" not in ids


def test_literal_argv_list_is_not_flagged_dynamic() -> None:
    """A literal argv list like ``["ls"]`` is static, so no AST008 is raised."""
    ids = _ids('import subprocess\nsubprocess.run(["ls", "-la"])\n')
    assert "AST001" in ids
    assert "AST008" not in ids


def test_safe_script_has_no_hits() -> None:
    """A benign data-processing script yields no findings (no false positive)."""
    script = (
        "import math\n"
        "def area(r):\n"
        "    return math.pi * r * r\n"
        "print(area(2))\n"
    )
    assert scan_python(script, default_policy()) == []


def test_unparseable_script_yields_ast000() -> None:
    """A syntax error produces a single review-level AST000 (never silent allow)."""
    hits = scan_python("def (:\n", default_policy())
    assert len(hits) == 1
    assert hits[0].rule_id == "AST000"
    assert hits[0].risk_level.value == "medium"


def test_recursive_directory_removal_detected() -> None:
    """``shutil.rmtree`` is flagged as a high-risk destructive file op."""
    assert "AST006" in _ids("import shutil\nshutil.rmtree('/data')\n")


def test_open_without_arguments_is_ignored() -> None:
    """``open()`` with no arguments cannot match a forbidden path."""
    assert "AST003" not in _ids("open()\n")


def test_deeply_nested_attribute_call_resolves() -> None:
    """A 3+ level dotted call is walked without error and stays benign."""
    assert _ids("import os\nos.path.join('a', 'b')\n") == set()


def test_process_call_without_args_is_not_dynamic() -> None:
    """A process spawn with no arguments is flagged but not review-level."""
    ids = _ids("import subprocess\nsubprocess.run()\n")
    assert "AST001" in ids
    assert "AST008" not in ids


def test_getattr_with_single_arg_is_not_obfuscation() -> None:
    """``getattr`` with fewer than two arguments is not treated as obfuscation."""
    assert "AST007" not in _ids("getattr(obj)\n")


def test_infinite_loop_with_only_nested_loop_still_flagged() -> None:
    """A ``break`` inside a *nested* loop does not terminate the outer ``while``."""
    script = "while True:\n    if cond:\n        for i in x:\n            pass\n"
    assert "AST005" in _ids(script)


def test_infinite_loop_with_deeply_nested_break_not_flagged() -> None:
    """A ``break`` nested two levels deep still terminates the ``while``."""
    script = "while True:\n    if a:\n        if b:\n            break\n"
    assert "AST005" not in _ids(script)


def test_dynamic_network_destination_adds_ast009() -> None:
    """A network call with a non-literal destination emits the medium AST009 hit."""
    ids = _ids("import requests\nrequests.post(exfil_url)\n")
    assert "AST004" in ids
    assert "AST009" in ids


def test_literal_network_destination_has_no_ast009() -> None:
    """A string-literal destination is statically verifiable, so no AST009."""
    ids = _ids('import requests\nrequests.get("https://api.openai.com")\n')
    assert "AST004" in ids
    assert "AST009" not in ids
