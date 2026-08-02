# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the tool safety CLI."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

from tests.tools.safety.tool_safety_check import main
from tests.tools.safety.tool_safety_manifest_report import main as manifest_main

REPO_ROOT = Path(__file__).resolve().parents[3]
CHECK_SCRIPT = Path(__file__).resolve().parent / "tool_safety_check.py"
MANIFEST_SCRIPT = Path(__file__).resolve().parent / "tool_safety_manifest_report.py"


def test_cli_enforces_timeout_policy(tmp_path):
    script_path = tmp_path / "safe.py"
    report_path = tmp_path / "report.json"
    script_path.write_text("print('ok')\n", encoding="utf-8")

    exit_code = main([
        "--script",
        str(script_path),
        "--language",
        "python",
        "--timeout",
        "999999",
        "--output",
        str(report_path),
    ])

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert report["decision"] == "needs_human_review"
    assert report["findings"][0]["rule_id"] == "RESOURCE_TIMEOUT_LIMIT_EXCEEDED"


def test_cli_scans_command_args(tmp_path):
    script_path = tmp_path / "empty.sh"
    report_path = tmp_path / "report.json"
    script_path.write_text("", encoding="utf-8")

    exit_code = main([
        "--script",
        str(script_path),
        "--language",
        "bash",
        "--command-args",
        "rm -rf /",
        "--output",
        str(report_path),
    ])

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert report["decision"] == "deny"
    assert report["findings"][0]["rule_id"] == "BASH_RECURSIVE_DELETE"


def test_cli_scans_stdin(monkeypatch, tmp_path):
    report_path = tmp_path / "report.json"
    monkeypatch.setattr("sys.stdin", io.StringIO("rm -rf /\n"))

    exit_code = main([
        "--script",
        "-",
        "--language",
        "bash",
        "--output",
        str(report_path),
    ])

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert report["decision"] == "deny"
    assert report["findings"][0]["rule_id"] == "BASH_RECURSIVE_DELETE"


def test_cli_scans_sample_directory(tmp_path):
    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    (samples_dir / "safe.py").write_text("print('ok')\n", encoding="utf-8")
    (samples_dir / "danger.sh").write_text("rm -rf /\n", encoding="utf-8")
    report_path = tmp_path / "all_reports.json"

    exit_code = main([
        "--samples",
        str(samples_dir),
        "--output",
        str(report_path),
    ])

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert report["sample_count"] == 2
    assert report["decisions"]["allow"] == 1
    assert report["decisions"]["deny"] == 1
    assert {item["tool_name"] for item in report["reports"]} == {"safe.py", "danger.sh"}


def test_manifest_report_validates_public_samples(tmp_path):
    report_path = tmp_path / "all_reports.json"

    exit_code = manifest_main(["--strict-policy", "--output", str(report_path)])

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["summary"]["sample_count"] >= 40
    assert report["summary"]["sample_count"] == report["summary"]["decision_matches"]
    assert report["summary"]["sample_count"] == report["summary"]["required_rule_matches"]
    assert report["summary"]["critical_category_checks"]["secret_read_no_allow"] is True
    assert report["summary"]["critical_category_checks"]["dangerous_delete_no_allow"] is True
    assert report["summary"]["critical_category_checks"]["non_whitelisted_network_no_allow"] is True
    assert report["generated_at"] == "1970-01-01T00:00:00+00:00"


def test_check_script_file_writes_expected_report(tmp_path):
    script_path = tmp_path / "danger.sh"
    report_path = tmp_path / "report.json"
    script_path.write_text("rm -rf /\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(CHECK_SCRIPT),
            "--script",
            str(script_path),
            "--language",
            "bash",
            "--output",
            str(report_path),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert result.returncode == 2
    assert report["decision"] == "deny"
    assert report["blocked"] is True
    assert report["tool_name"] == "tool_safety_cli"
    assert [finding["rule_id"] for finding in report["findings"]] == ["BASH_RECURSIVE_DELETE"]


def test_manifest_script_file_writes_expected_summary(tmp_path):
    report_path = tmp_path / "all_reports.json"

    result = subprocess.run(
        [
            sys.executable,
            str(MANIFEST_SCRIPT),
            "--strict-policy",
            "--output",
            str(report_path),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert result.returncode == 0
    assert json.loads(result.stdout)["passed"] is True
    assert report["generated_at"] == "1970-01-01T00:00:00+00:00"
    assert report["summary"] == {
        "sample_count": 40,
        "decision_matches": 40,
        "required_rule_matches": 40,
        "decisions": {
            "allow": 4,
            "deny": 23,
            "needs_human_review": 13,
        },
        "critical_category_checks": {
            "dangerous_delete_no_allow": True,
            "non_whitelisted_network_no_allow": True,
            "safe_no_deny": True,
            "secret_read_no_allow": True,
        },
        "passed": True,
    }
    assert report["mismatches"] == []
