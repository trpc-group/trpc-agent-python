# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Acceptance tests for the public tool safety sample corpus."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CLI = REPOSITORY_ROOT / "scripts" / "tool_safety_check.py"
EXAMPLE_ROOT = REPOSITORY_ROOT / "examples" / "tool_safety"
SAMPLE_ROOT = EXAMPLE_ROOT / "samples"
POLICY = EXAMPLE_ROOT / "tool_safety_policy.yaml"
MANIFEST = yaml.safe_load((SAMPLE_ROOT / "manifest.yaml").read_text(encoding="utf-8"))
SAMPLE_CASES = MANIFEST["samples"]
EXIT_CODES = {
    "allow": 0,
    "deny": 1,
    "needs_human_review": 2,
}


def _run_cli(*arguments: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *(str(argument) for argument in arguments)],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("sample", SAMPLE_CASES, ids=lambda sample: sample["scenario"])
def test_public_sample_matches_manifest(sample, tmp_path):
    report_path = tmp_path / "report.json"
    audit_path = tmp_path / "audit.jsonl"
    result = _run_cli(
        SAMPLE_ROOT / sample["file"],
        "--policy",
        POLICY,
        "--report",
        report_path,
        "--audit",
        audit_path,
        "--tool-name",
        "public-sample-test",
    )

    assert result.returncode == EXIT_CODES[sample["expected_decision"]], result.stdout + result.stderr
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert json.loads(result.stdout) == payload
    assert payload["files_scanned"] == 1
    report = payload["reports"][0]["report"]
    assert report["decision"] == sample["expected_decision"]
    assert set(sample["expected_rules"]).issubset(report["rule_ids"])
    assert {
        "decision",
        "risk_level",
        "rule_ids",
        "findings",
        "duration_ms",
        "script_sha256",
        "redacted",
        "blocked",
    }.issubset(report)
    for finding in report["findings"]:
        assert finding["rule_id"]
        assert finding["evidence"]
        assert finding["recommendation"]

    audit_events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert len(audit_events) == 1
    assert audit_events[0]["decision"] == sample["expected_decision"]
    assert audit_events[0]["tool_name"] == "public-sample-test"
    assert set(sample["expected_rules"]).issubset(audit_events[0]["rule_ids"])


def test_public_corpus_meets_detection_and_false_positive_thresholds(tmp_path):
    assert len(SAMPLE_CASES) == 12
    report_path = tmp_path / "all-reports.json"
    audit_path = tmp_path / "all-audit.jsonl"
    result = _run_cli(
        SAMPLE_ROOT,
        "--policy",
        POLICY,
        "--report",
        report_path,
        "--audit",
        audit_path,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    reports_by_file = {Path(entry["path"]).name: entry["report"] for entry in payload["reports"]}
    assert set(reports_by_file) == {sample["file"] for sample in SAMPLE_CASES}

    detection_cases = [sample for sample in SAMPLE_CASES if sample["required_detection"]]
    detected = sum(reports_by_file[sample["file"]]["decision"] != "allow" for sample in detection_cases)
    assert detected / len(detection_cases) >= 0.90

    safe_cases = [sample for sample in SAMPLE_CASES if sample["safe"]]
    false_positives = sum(reports_by_file[sample["file"]]["decision"] != "allow" for sample in safe_cases)
    assert false_positives / len(safe_cases) <= 0.10

    mandatory_rules = {
        "dangerous_delete": "FILE-DANGEROUS-DELETE",
        "read_secret_file": "FILE-DENIED-PATH",
        "non_allowlisted_network": "NET-NON-WHITELISTED",
    }
    mandatory_hits = 0
    for sample in SAMPLE_CASES:
        required_rule = mandatory_rules.get(sample["scenario"])
        if required_rule is None:
            continue
        report = reports_by_file[sample["file"]]
        assert report["decision"] == "deny"
        mandatory_hits += required_rule in report["rule_ids"]
    assert mandatory_hits / len(mandatory_rules) == 1.0

    audit_events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert len(audit_events) == len(SAMPLE_CASES)
