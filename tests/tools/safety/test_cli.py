# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the tool safety CLI."""

from __future__ import annotations

import io
import json

from scripts.tool_safety_check import main
from scripts.tool_safety_manifest_report import main as manifest_main


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
