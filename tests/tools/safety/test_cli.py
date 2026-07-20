"""Tests for the safety CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI = REPO_ROOT / "scripts" / "tool_safety_check.py"
POLICY = (REPO_ROOT / "trpc_agent_sdk" / "tools" / "safety" / "examples" / "tool_safety_policy.yaml")


def _run_cli(*args: str, audit_file: Path) -> tuple[int, str, str]:
    cmd = [
        sys.executable,
        str(CLI),
        "--policy",
        str(POLICY),
        *args,
        "--audit-file",
        str(audit_file),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
    return proc.returncode, proc.stdout, proc.stderr


def test_single_allow_exit_0(tmp_path):
    rc, out, err = _run_cli(
        "--language",
        "python",
        "--script",
        "print('hello')",
        "--tool-name",
        "test",
        audit_file=tmp_path / "audit.jsonl",
    )
    assert rc == 0
    payload = json.loads(out)
    assert payload["decision"] == "allow"


def test_single_deny_exit_2(tmp_path):
    rc, out, err = _run_cli(
        "--language",
        "python",
        "--script",
        "import shutil\nshutil.rmtree('/tmp/x')",
        "--tool-name",
        "test",
        audit_file=tmp_path / "audit.jsonl",
    )
    assert rc == 2
    payload = json.loads(out)
    assert payload["decision"] == "deny"


def test_review_exit_3(tmp_path):
    rc, out, err = _run_cli(
        "--language",
        "bash",
        "--script",
        "ls | grep foo",
        audit_file=tmp_path / "audit.jsonl",
    )
    assert rc == 3


def test_invalid_policy_exit_4(tmp_path):
    missing_policy = tmp_path / "missing.yaml"
    cmd = [sys.executable, str(CLI), "--policy", str(missing_policy), "--script", "print(1)"]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 4
    assert "policy error:" in proc.stderr
    assert str(missing_policy) in proc.stderr
    assert proc.stdout == ""


def test_required_audit_failure_exits_4(tmp_path):
    rc, out, err = _run_cli(
        "--language",
        "python",
        "--script",
        "print('hello')",
        audit_file=tmp_path,
    )
    assert rc == 4
    assert "audit error:" in err


def test_manifest_writes_output(tmp_path):
    manifest = (REPO_ROOT / "trpc_agent_sdk" / "tools" / "safety" / "examples" / "samples" / "manifest.yaml")
    output = tmp_path / "out.json"
    audit = tmp_path / "audit.jsonl"
    cmd = [
        sys.executable,
        str(CLI), "--policy",
        str(POLICY), "--manifest",
        str(manifest), "--manifest-output",
        str(output), "--audit-file",
        str(audit)
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert len(payload) == 14
    for item in payload:
        report = item["report"]
        assert {
            "decision",
            "risk_level",
            "rule_ids",
            "findings",
            "recommendation",
        } <= report.keys()
        if report["decision"] != "allow":
            assert report["findings"]
        for finding in report["findings"]:
            assert {
                "category",
                "rule_id",
                "evidence",
                "recommendation",
            } <= finding.keys()
    # Audit file has one line per sample.
    lines = [ln for ln in audit.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 14
