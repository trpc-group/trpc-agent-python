"""Deterministic tests for the phase-1/2 code review example."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent.diff_parser import ParsedDiff
from agent.diff_parser import parse_unified_diff
from agent.filtering import evaluate_filter_decision
from agent.findings import Finding
from agent.report import build_report
from agent.report import write_reports
from agent.rules import run_static_rules
from agent.sandbox import DryRunSandboxRunner
from agent.storage import persist_review
from agent.telemetry import build_telemetry_summary


EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = EXAMPLE_ROOT / "fixtures"


def _review_fixture(name: str) -> tuple[ParsedDiff, list[Finding]]:
    diff_text = (FIXTURES / name).read_text(encoding="utf-8")
    parsed = parse_unified_diff(diff_text)
    findings = run_static_rules(parsed.changed_lines)
    return parsed, findings


def _review_fixture_with_text(name: str) -> tuple[str, ParsedDiff, list[Finding]]:
    diff_text = (FIXTURES / name).read_text(encoding="utf-8")
    parsed = parse_unified_diff(diff_text)
    findings = run_static_rules(parsed.changed_lines)
    return diff_text, parsed, findings


def _categories(findings: list[Finding]) -> set[str]:
    return {finding.category for finding in findings}


def _count_category(findings: list[Finding], category: str) -> int:
    return sum(1 for finding in findings if finding.category == category)


def test_clean_diff_has_no_high_findings() -> None:
    _, findings = _review_fixture("clean.diff")
    assert [finding for finding in findings if finding.severity == "high"] == []


def test_security_diff_contains_expected_categories() -> None:
    _, findings = _review_fixture("security.diff")
    categories = _categories(findings)
    assert "secret" in categories
    assert "network-timeout" in categories
    assert "error-handling" in categories


def test_sql_injection_fixture() -> None:
    _, findings = _review_fixture("sql_injection.diff")
    assert "sql-injection" in _categories(findings)


def test_missing_timeout_fixture() -> None:
    _, findings = _review_fixture("missing_timeout.diff")
    assert "network-timeout" in _categories(findings)


def test_broad_except_fixture() -> None:
    _, findings = _review_fixture("broad_except.diff")
    assert "error-handling" in _categories(findings)


def test_resource_leak_fixture() -> None:
    _, findings = _review_fixture("resource_leak.diff")
    assert "resource-lifecycle" in _categories(findings)


def test_secret_redaction_report_omits_plaintext_values(tmp_path: Path) -> None:
    parsed, findings = _review_fixture("secret_redaction.diff")
    report = build_report(
        diff_file="fixtures/secret_redaction.diff",
        files=parsed.files,
        findings=findings,
        dry_run=True,
    )
    json_path, md_path = write_reports(report, tmp_path)
    combined = json_path.read_text(encoding="utf-8") + md_path.read_text(encoding="utf-8")

    assert "sk-test-abcdefghijklmnop" not in combined
    assert "token-value-1234567890" not in combined
    assert "correct-horse-prod-password" not in combined
    assert "<redacted:" in combined


def test_duplicate_fixture_dedupes_repeated_finding() -> None:
    _, findings = _review_fixture("duplicate.diff")
    assert _count_category(findings, "network-timeout") == 1


def test_default_filter_decision_is_allow() -> None:
    diff_text, parsed, _ = _review_fixture_with_text("clean.diff")
    decision = evaluate_filter_decision(diff_text, parsed)
    assert decision.decision == "allow"


def test_sandbox_dry_run_status_is_completed() -> None:
    diff_text, parsed, findings = _review_fixture_with_text("security.diff")
    decision = evaluate_filter_decision(diff_text, parsed)
    sandbox_run = DryRunSandboxRunner().run(
        files=parsed.files,
        findings=findings,
        filter_decision=decision,
    )
    assert sandbox_run.runner_name == "dry-run"
    assert sandbox_run.status == "completed"


def test_telemetry_summary_contains_findings_counts() -> None:
    diff_text, parsed, findings = _review_fixture_with_text("security.diff")
    decision = evaluate_filter_decision(diff_text, parsed)
    sandbox_run = DryRunSandboxRunner().run(
        files=parsed.files,
        findings=findings,
        filter_decision=decision,
    )
    telemetry = build_telemetry_summary(
        files_scanned=parsed.files,
        findings=findings,
        sandbox_run=sandbox_run,
        filter_decision=decision,
        duration_ms=12,
    )
    assert telemetry["total_findings"] == len(findings)
    assert telemetry["severity_counts"]["high"] >= 1


def test_sqlite_persists_sandbox_and_filter_rows(tmp_path: Path) -> None:
    diff_text, parsed, findings = _review_fixture_with_text("security.diff")
    decision = evaluate_filter_decision(diff_text, parsed)
    sandbox_run = DryRunSandboxRunner().run(
        files=parsed.files,
        findings=findings,
        filter_decision=decision,
    )
    telemetry = build_telemetry_summary(
        files_scanned=parsed.files,
        findings=findings,
        sandbox_run=sandbox_run,
        filter_decision=decision,
        duration_ms=25,
    )
    report = build_report(
        diff_file="fixtures/security.diff",
        files=parsed.files,
        findings=findings,
        dry_run=True,
        filter_summary=decision.to_dict(),
        sandbox_summary=sandbox_run.to_dict(),
        telemetry_summary=telemetry,
    )
    json_path, md_path = write_reports(report, tmp_path)
    db_path = tmp_path / "reviews.sqlite3"
    task_id = persist_review(
        db_path=db_path,
        report=report,
        json_report_path=json_path,
        markdown_report_path=md_path,
    )

    with sqlite3.connect(db_path) as conn:
        sandbox_count = conn.execute("select count(*) from sandbox_runs where task_id = ?", (task_id, )).fetchone()[0]
        filter_count = conn.execute("select count(*) from filter_decisions where task_id = ?", (task_id, )).fetchone()[0]
        sandbox_status = conn.execute("select status from sandbox_runs where task_id = ?", (task_id, )).fetchone()[0]
        filter_decision = conn.execute("select decision from filter_decisions where task_id = ?", (task_id, )).fetchone()[0]

    assert sandbox_count == 1
    assert filter_count == 1
    assert sandbox_status == "completed"
    assert filter_decision == "allow"
