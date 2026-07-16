# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the sandbox checker scripts (run host-side as plain subprocesses)."""
import json
import subprocess
import sys
from pathlib import Path

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = EXAMPLE_ROOT / "fixtures"
SCRIPTS = EXAMPLE_ROOT / "skills" / "code-review" / "scripts"


def run_checker(script, fixture):
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / script), str(FIXTURES / fixture)],
        capture_output=True, text=True, check=True)
    return json.loads(out.stdout)["findings"]


def test_security_checker_detects_eval_shell_sql():
    findings = run_checker("check_security.py", "security_eval.diff")
    titles = " | ".join(f["title"] for f in findings)
    assert any(f["category"] == "security" for f in findings)
    assert "eval" in titles.lower()
    assert any(f["line"] == 11 for f in findings)
    assert all(f["source"] == "static" for f in findings)


def test_security_checker_clean_diff_is_quiet():
    assert run_checker("check_security.py", "clean.diff") == []


def test_async_leak_checker():
    findings = run_checker("check_async_leak.py", "async_leak.diff")
    assert any(f["category"] == "async_resource_leak" for f in findings)
    assert len(findings) >= 2


def test_db_lifecycle_checker():
    findings = run_checker("check_db_lifecycle.py", "db_lifecycle.diff")
    assert any(f["category"] == "db_lifecycle" for f in findings)
    assert any("connect" in f["title"].lower() for f in findings)


def test_tests_missing_checker_fires_without_test_changes():
    findings = run_checker("check_tests_missing.py", "missing_test.diff")
    assert len(findings) == 1
    assert findings[0]["category"] == "missing_test"
    assert findings[0]["file"] == "app/service.py"


def test_tests_missing_checker_quiet_when_tests_changed():
    assert run_checker("check_tests_missing.py", "clean.diff") == []


def test_secrets_checker_redacts_evidence():
    findings = run_checker("check_secrets.py", "secret_leak.diff")
    assert any(f["category"] == "secret_leak" for f in findings)
    for f in findings:
        assert "sk-fakefakefakefakefakefake123456" not in f["evidence"]
        assert "AKIA0123456789ABCDEF" not in f["evidence"]
        assert "***REDACTED-" in f["evidence"]


def test_duplicate_fixture_produces_two_security_findings_same_line():
    findings = run_checker("check_security.py", "duplicate_finding.diff")
    keys = [(f["file"], f["line"], f["category"]) for f in findings]
    assert len(keys) == 2
    assert keys[0] == keys[1]
