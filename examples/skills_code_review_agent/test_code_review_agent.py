"""Acceptance coverage for the eight public review scenarios."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from agent import CodeReviewAgent
from diff_parser import DiffParser
from rules import RuleEngine
from sandbox import SandboxRunner


HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
POLICY = HERE / "review_policy.yaml"


def make_agent(tmp_path) -> CodeReviewAgent:
    return CodeReviewAgent(tmp_path, tmp_path / "reviews.db", POLICY, dry_run=True)


def review_fixture(tmp_path, name: str, commands=None):
    agent = make_agent(tmp_path)
    diff = (FIXTURES / name).read_text(encoding="utf-8")
    report = agent.review_diff(diff, tmp_path / "output", commands=commands)
    return agent, report


def test_clean_diff_has_no_high_confidence_findings(tmp_path):
    _, report = review_fixture(tmp_path, "clean.diff")
    assert report.conclusion == "approve"
    assert report.findings == []


def test_security_diff_is_detected(tmp_path):
    _, report = review_fixture(tmp_path, "security.diff")
    assert any(item.rule_id == "SEC001" and item.severity == "critical" for item in report.findings)
    assert report.conclusion == "changes_requested"


def test_async_resource_issues_are_detected(tmp_path):
    _, report = review_fixture(tmp_path, "async_leak.diff")
    async_findings = [item for item in report.findings if item.category == "async_error"]
    assert len(async_findings) == 2
    assert all(item.recommendation for item in async_findings)


def test_database_connection_lifecycle_is_detected(tmp_path):
    _, report = review_fixture(tmp_path, "database.diff")
    assert any(item.rule_id == "DB001" for item in report.findings)


def test_missing_tests_are_routed_to_human_review(tmp_path):
    _, report = review_fixture(tmp_path, "test_missing.diff")
    assert report.findings == []
    assert any(item.rule_id == "TEST001" for item in report.warnings)
    assert report.conclusion == "needs_human_review"


def test_duplicate_findings_are_suppressed():
    _, lines = DiffParser.from_file(FIXTURES / "duplicate.diff")
    findings = RuleEngine().scan([*lines, *lines])
    assert len([item for item in findings if item.rule_id == "SEC001"]) == 1


def test_sandbox_failure_is_persisted_without_crashing_review(tmp_path):
    agent, report = review_fixture(tmp_path, "sandbox_failure.diff", [["fake-fail"]])
    assert report.status == "completed_with_errors"
    assert report.sandbox_runs[0].status == "failed"
    stored = agent.store.get_task(report.task_id)
    assert stored["sandbox_runs"][0]["error_type"] == "process_error"
    assert stored["report"] is not None


def test_secret_is_detected_and_never_persisted_in_plaintext(tmp_path):
    agent, report = review_fixture(tmp_path, "secret.diff")
    assert any(item.rule_id == "SECRET001" for item in report.findings)
    report_text = (tmp_path / "output" / "review_report.json").read_text(encoding="utf-8")
    database_bytes = (tmp_path / "reviews.db").read_bytes()
    assert "sk-super-secret-value" not in report_text
    assert b"sk-super-secret-value" not in database_bytes
    assert "[REDACTED]" in report_text
    assert agent.store.get_task(report.task_id)["findings"]


def test_filter_blocks_unapproved_network_before_sandbox(tmp_path):
    agent, report = review_fixture(
        tmp_path, "clean.diff", [["curl", "https://untrusted.example/data"]]
    )
    assert report.sandbox_runs[0].status == "blocked"
    assert report.sandbox_runs[0].filter_decision in ("deny", "needs_human_review")
    assert report.monitoring["blocked_count"] == 1
    assert agent.store.get_task(report.task_id)["filter_blocks"]


def test_container_timeout_becomes_failed_run(monkeypatch, tmp_path):
    runner = SandboxRunner(POLICY, tmp_path, dry_run=False)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], timeout=1)

    monkeypatch.setattr(subprocess, "run", timeout)
    result = runner.run(["python", "check.py"], 1)
    assert result.status == "failed"
    assert result.error_type == "timeout"


def test_sandbox_output_is_capped_and_redacted(monkeypatch, tmp_path):
    runner = SandboxRunner(POLICY, tmp_path, dry_run=False)
    runner.sandbox["max_output_bytes"] = 80
    secret_output = "api_key=sk-super-secret-value " + "x" * 200

    def completed(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout=secret_output, stderr="")

    monkeypatch.setattr(subprocess, "run", completed)
    result = runner.run(["python", "check.py"], 1)
    assert len(result.output.encode("utf-8")) <= 80
    assert "sk-super-secret-value" not in result.output
    assert result.redacted is True


def test_command_arguments_are_redacted_before_audit(tmp_path):
    runner = SandboxRunner(POLICY, tmp_path, dry_run=True)
    result = runner.run(["python", "check.py", "api_key=sk-super-secret-value"], 1)
    assert "sk-super-secret-value" not in " ".join(result.command)
    assert "[REDACTED]" in " ".join(result.command)


def test_all_eight_fixtures_parse_and_fake_flow_is_under_two_minutes(tmp_path):
    started = time.perf_counter()
    names = sorted(path.name for path in FIXTURES.glob("*.diff"))
    assert len(names) == 8
    for index, name in enumerate(names):
        review_fixture(tmp_path / str(index), name)
    assert time.perf_counter() - started < 120


def test_machine_report_contains_required_sections(tmp_path):
    _, report = review_fixture(tmp_path, "security.diff")
    payload = json.loads((tmp_path / "output" / "review_report.json").read_text(encoding="utf-8"))
    assert {"findings", "warnings", "filter_blocks", "sandbox_runs", "monitoring"} <= payload.keys()
    assert {
        "severity", "category", "file", "line", "title", "evidence",
        "recommendation", "confidence", "source",
    } <= payload["findings"][0].keys()
