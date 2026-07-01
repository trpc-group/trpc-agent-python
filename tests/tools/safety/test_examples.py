"""Tests for Tool Safety example report and audit artifacts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from trpc_agent_sdk._tool_safety import SafetyReviewer

_EXAMPLE_CASES = {
    "allow_python": {
        "path": Path("examples/tool_safety/samples/allow.py"),
        "action_type": "python",
        "source": "print('hello from tool safety')\n",
    },
    "deny_bash": {
        "path": Path("examples/tool_safety/samples/deny.sh"),
        "action_type": "bash",
        "source": (
            "# Inert sample: rm -rf /tmp/demo\n"
            "printf '%s\\n' 'destructive delete sample is intentionally not executed'\n"
        ),
    },
    "needs_human_review_bash": {
        "path": Path("examples/tool_safety/samples/needs_human_review.sh"),
        "action_type": "bash",
        "source": (
            "# Inert sample: npm install left-pad\n"
            "printf '%s\\n' 'dependency install sample is intentionally not executed'\n"
        ),
    },
}


def test_example_report_matches_current_cli_report_schema() -> None:
    cli = _load_cli_module()
    report = _read_example_report()
    reports = report["reports"]

    assert report["schema_version"] == 1
    assert {item["decision"] for item in reports} == {"allow", "deny", "needs_human_review"}

    reviewer = SafetyReviewer()
    for item in reports:
        case = _EXAMPLE_CASES[item["case"]]
        review = reviewer.review(
            case["source"],
            action_type=case["action_type"],
            tool_name="tool_safety_check",
        )
        expected = {
            "case": item["case"],
            "action_type": case["action_type"],
            **cli._build_report(review, case["path"]),
        }
        assert item == expected


def test_example_audit_jsonl_lines_are_valid_json() -> None:
    records = _read_example_audit_records()

    assert len(records) >= 3
    assert {record["decision"] for record in records} >= {"allow", "deny", "needs_human_review"}
    for record in records:
        for key in {
            "tool_name",
            "decision",
            "risk_level",
            "rule_id",
            "blocked",
            "latency",
            "timestamp",
            "input_sha256",
        }:
            assert key in record
        assert isinstance(record["blocked"], bool)
        assert isinstance(record["latency"], (int, float))
        assert isinstance(record["timestamp"], str)
        assert isinstance(record["input_sha256"], str)
        assert len(record["input_sha256"]) == 64


def test_example_audit_matches_current_reviewer_stable_fields() -> None:
    records = _read_example_audit_records()
    records_by_case = {record["case"]: record for record in records}
    reviewer = SafetyReviewer()

    for case_name, case in _EXAMPLE_CASES.items():
        review = reviewer.review(
            case["source"],
            action_type=case["action_type"],
            tool_name="tool_safety_check",
        )
        record = records_by_case[case_name]
        for key in {
            "tool_name",
            "decision",
            "risk_level",
            "rule_id",
            "blocked",
            "desensitized",
            "action_type",
            "input_sha256",
            "allowed_domains",
            "rules_evaluated",
        }:
            assert record[key] == review.audit[key]


def test_example_files_can_be_reloaded() -> None:
    report = _read_example_report()
    records = _read_example_audit_records()

    assert isinstance(report, dict)
    assert isinstance(report["reports"], list)
    assert all(isinstance(record, dict) for record in records)


def test_cli_scans_public_example_scripts(capsys) -> None:
    cli = _load_cli_module()
    project_root = Path(__file__).resolve().parents[3]

    for case, expected_exit_code, expected_decision in (
        (_EXAMPLE_CASES["allow_python"], 0, "allow"),
        (_EXAMPLE_CASES["deny_bash"], 1, "deny"),
        (_EXAMPLE_CASES["needs_human_review_bash"], 2, "needs_human_review"),
    ):
        exit_code = cli.main([str(project_root / case["path"])])
        output = json.loads(capsys.readouterr().out)

        assert exit_code == expected_exit_code
        assert output["decision"] == expected_decision
        assert output["path"].endswith(str(case["path"]))


def _load_cli_module():
    script_path = Path(__file__).resolve().parents[3] / "scripts" / "tool_safety_check.py"
    spec = importlib.util.spec_from_file_location("tool_safety_check", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _read_example_report() -> dict:
    report_path = Path(__file__).resolve().parents[3] / "examples" / "tool_safety" / "tool_safety_report.json"
    return json.loads(report_path.read_text(encoding="utf-8"))


def _read_example_audit_records() -> list[dict]:
    audit_path = Path(__file__).resolve().parents[3] / "examples" / "tool_safety" / "tool_safety_audit.jsonl"
    return [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
