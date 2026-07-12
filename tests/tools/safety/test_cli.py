# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""CLI tests for the public tool safety scanner."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import yaml
from scripts import tool_safety_check
from trpc_agent_sdk.tools.safety import SafetyScanRequest
from trpc_agent_sdk.tools.safety import ScriptLanguage
from trpc_agent_sdk.tools.safety import ToolSafetyScanner

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CLI = REPOSITORY_ROOT / "scripts" / "tool_safety_check.py"
EXAMPLE_ROOT = REPOSITORY_ROOT / "examples" / "tool_safety"
SAMPLES = EXAMPLE_ROOT / "samples"
POLICY = EXAMPLE_ROOT / "tool_safety_policy.yaml"


def _run_cli(*arguments: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *(str(argument) for argument in arguments)],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_cli_single_file_exit_codes_and_json_report(tmp_path):
    cases = [
        ("01_safe_python.py", 0, "allow"),
        ("02_dangerous_delete.sh", 1, "deny"),
        ("06_subprocess_call.py", 2, "needs_human_review"),
    ]

    for file_name, expected_code, expected_decision in cases:
        report_path = tmp_path / f"{file_name}.json"
        result = _run_cli(SAMPLES / file_name, "--policy", POLICY, "--report", report_path)

        assert result.returncode == expected_code, result.stdout + result.stderr
        stdout_payload = json.loads(result.stdout)
        report_payload = json.loads(report_path.read_text(encoding="utf-8"))
        assert stdout_payload == report_payload
        assert report_payload["decision"] == expected_decision
        assert report_payload["files_scanned"] == 1
        assert report_payload["reports"][0]["report"]["decision"] == expected_decision


def test_cli_multiple_inputs_use_strictest_decision(tmp_path):
    report_path = tmp_path / "combined.json"
    audit_path = tmp_path / "audit.jsonl"
    result = _run_cli(
        SAMPLES / "01_safe_python.py",
        SAMPLES / "06_subprocess_call.py",
        "--policy",
        POLICY,
        "--report",
        report_path,
        "--audit",
        audit_path,
        "--tool-name",
        "cli-test",
    )

    assert result.returncode == 2, result.stdout + result.stderr
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["decision"] == "needs_human_review"
    assert payload["files_scanned"] == 2
    audit_events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert len(audit_events) == 2
    assert {event["tool_name"] for event in audit_events} == {"cli-test"}
    assert all(event["redacted"] is True for event in audit_events)


def test_cli_directory_scan_is_recursive_and_deduplicated(tmp_path):
    report_path = tmp_path / "directory.json"
    result = _run_cli(SAMPLES, SAMPLES / "01_safe_python.py", "--policy", POLICY, "--report", report_path)

    assert result.returncode == 1, result.stdout + result.stderr
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["files_scanned"] == 12
    paths = [entry["path"] for entry in payload["reports"]]
    assert paths == sorted(paths)
    assert len(paths) == len(set(paths))


def test_cli_policy_change_updates_domain_allowlist_without_code_change(tmp_path):
    policy_data = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    policy_data["allowed_domains"] = ["external.example"]
    policy_path = tmp_path / "overridden-policy.yaml"
    policy_path.write_text(yaml.safe_dump(policy_data, sort_keys=False), encoding="utf-8")

    result = _run_cli(SAMPLES / "04_non_allowlisted_network.py", "--policy", policy_path)

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["decision"] == "allow"
    assert "NET-NON-WHITELISTED" not in payload["reports"][0]["report"]["rule_ids"]


def test_cli_rejects_unsupported_input_with_structured_fail_closed_report(tmp_path):
    unsupported = tmp_path / "payload.txt"
    unsupported.write_text("rm -rf /", encoding="utf-8")
    report_path = tmp_path / "error.json"

    result = _run_cli(unsupported, "--policy", POLICY, "--report", report_path)

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload == json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["decision"] == "deny"
    assert payload["files_scanned"] == 0
    assert payload["error"]["type"] == "ToolSafetyCliError"


def test_cli_audit_append_completes_short_writes(tmp_path, monkeypatch):
    report = ToolSafetyScanner().scan(
        SafetyScanRequest(script="print('ok')", language=ScriptLanguage.PYTHON, tool_name="short-write"))
    audit_path = tmp_path / "audit.jsonl"
    original_write = os.write

    def short_write(fd, payload):
        chunk_size = max(1, len(payload) // 2)
        return original_write(fd, payload[:chunk_size])

    monkeypatch.setattr("trpc_agent_sdk.tools.safety._audit.os.write", short_write)

    tool_safety_check._append_audit_events(audit_path, [report, report])

    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    assert all(record["tool_name"] == "short-write" for record in records)
