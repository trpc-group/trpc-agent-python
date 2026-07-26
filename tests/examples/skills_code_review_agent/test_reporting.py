"""Tests for finding validation, deduplication, and report output."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
import json

import pytest

from examples.skills_code_review_agent.agent.models import Category
from examples.skills_code_review_agent.agent.models import Finding
from examples.skills_code_review_agent.agent.models import ReviewMetrics
from examples.skills_code_review_agent.agent.models import ReviewReport
from examples.skills_code_review_agent.agent.models import Severity
from examples.skills_code_review_agent.agent.models import TaskStatus
from examples.skills_code_review_agent.agent.policy import SecretRedactor
from examples.skills_code_review_agent.agent.reporting import FindingOutputError
from examples.skills_code_review_agent.agent.reporting import deduplicate_findings
from examples.skills_code_review_agent.agent.reporting import parse_findings_jsonl
from examples.skills_code_review_agent.agent.reporting import prepare_findings
from examples.skills_code_review_agent.agent.reporting import render_markdown
from examples.skills_code_review_agent.agent.reporting import write_report_files

SECRET = "sk-1234567890abcdefghijklmnop"
GENERIC_SECRET = "CorrectHorseBatteryStaple"


def _finding(**updates) -> Finding:
    values = {
        "severity": Severity.MEDIUM,
        "category": Category.SECURITY,
        "file": "app.py",
        "line": 3,
        "title": "Unsafe call",
        "evidence": "eval(value)",
        "recommendation": "Parse allowed values.",
        "confidence": 0.9,
        "source": "rule.security.eval",
    }
    values.update(updates)
    return Finding.model_validate(values)


def _report(**updates) -> ReviewReport:
    values = {
        "task_id":
        "task-report",
        "status":
        TaskStatus.COMPLETE,
        "input_summary": {
            "file_count": 1
        },
        "findings": [_finding()],
        "warnings": [],
        "needs_human_review": [],
        "filter_decisions": [],
        "sandbox_runs": [],
        "failures": [],
        "metrics":
        ReviewMetrics(
            total_duration_ms=1,
            sandbox_duration_ms=1,
            tool_calls=2,
            blocked_count=0,
            finding_count=1,
        ),
        "conclusion":
        "Review complete.",
        "created_at":
        datetime.now(timezone.utc),
    }
    values.update(updates)
    return ReviewReport.model_validate(values)


def test_parse_findings_requires_exact_contract() -> None:
    payload = _finding().model_dump(mode="json")
    assert parse_findings_jsonl(json.dumps(payload), SecretRedactor()) == [_finding()]
    payload["unexpected"] = True
    with pytest.raises(FindingOutputError, match="exactly"):
        parse_findings_jsonl(json.dumps(payload), SecretRedactor())


@pytest.mark.parametrize(
    "text,error",
    [
        ("not json", "invalid finding JSON"),
        (json.dumps({"nested": [[[[[[[[[[]]]]]]]]]]}), "exactly"),
    ],
)
def test_parse_findings_rejects_invalid_json(text: str, error: str) -> None:
    with pytest.raises(FindingOutputError, match=error):
        parse_findings_jsonl(text, SecretRedactor())


def test_deduplicate_merges_best_finding_and_sources() -> None:
    low = _finding(confidence=0.7, source="rule.one", evidence="first")
    high = _finding(
        severity=Severity.HIGH,
        confidence=0.95,
        source="rule.two",
        evidence="second",
    )
    result = deduplicate_findings([low, high])
    assert len(result) == 1
    assert result[0].severity == Severity.HIGH
    assert result[0].confidence == 0.95
    assert result[0].source == "rule.one | rule.two"
    assert result[0].evidence == "first | second"


def test_low_confidence_becomes_warning_and_human_review() -> None:
    buckets = prepare_findings(
        [_finding(confidence=0.2, evidence=SECRET)],
        SecretRedactor(),
    )
    assert not buckets.actionable
    assert buckets.warnings[0].severity == Severity.WARNING
    assert buckets.needs_human_review == buckets.warnings
    assert SECRET not in buckets.warnings[0].evidence


def test_render_markdown_has_fixed_sections() -> None:
    markdown = render_markdown(_report())
    for heading in (
            "## Task summary",
            "## Findings",
            "## Warnings / needs human review",
            "## Filter decisions",
            "## Sandbox runs",
            "## Exceptions",
            "## Metrics",
            "## Final conclusion",
    ):
        assert heading in markdown


def test_render_markdown_escapes_untrusted_finding_text() -> None:
    evidence = "<script>alert(1)</script>\n## forged heading"
    markdown = render_markdown(_report(findings=[_finding(evidence=evidence)]))

    assert "<script>" not in markdown
    assert "&lt;script&gt;" in markdown
    assert "\n## forged heading" not in markdown


def test_report_files_are_isolated_and_redacted(tmp_path) -> None:
    evidence = f'"password": "{GENERIC_SECRET}"'
    report = _report(
        conclusion=f"token={SECRET}",
        findings=[_finding(evidence=evidence)],
    )
    json_path, markdown_path, markdown = write_report_files(report, tmp_path)
    assert json_path.parent == tmp_path / report.task_id
    assert markdown_path.parent == json_path.parent
    json_report = json_path.read_text(encoding="utf-8")
    markdown_report = markdown_path.read_text(encoding="utf-8")
    for secret in (SECRET, GENERIC_SECRET):
        assert secret not in json_report
        assert secret not in markdown_report
        assert secret not in markdown
