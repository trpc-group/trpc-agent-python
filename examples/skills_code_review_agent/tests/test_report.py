"""Tests for report generation."""

import json

from pipeline.report import (
    build_recommendations,
    generate_json_report,
    generate_md_report,
)
from pipeline.types import Finding, FindingCategory, ReviewReport, Severity


def _make_sample_report(num_findings=3):
    findings = [
        Finding(
            severity=Severity.CRITICAL,
            category=FindingCategory.SECURITY,
            file="handler.py", line=8,
            title="Command injection via os.system",
            evidence="os.system(user_input)",
            recommendation="Use subprocess.run with shell=False",
            confidence=0.95, source="security_scanner",
        ),
        Finding(
            severity=Severity.HIGH,
            category=FindingCategory.RESOURCE_LEAK,
            file="worker.py", line=15,
            title="File handle leak",
            evidence="f = open('data.txt')",
            recommendation="Use 'with open(...)'",
            confidence=0.85, source="resource_leak_scanner",
        ),
        Finding(
            severity=Severity.LOW,
            category=FindingCategory.MISSING_TESTS,
            file="utils.py", line=3,
            title="Missing test for calculate_average",
            evidence="def calculate_average(values):",
            recommendation="Add test_calculate_average",
            confidence=0.3, source="missing_tests_scanner",
        ),
    ][:num_findings]

    return ReviewReport(
        task_id="test-report-001",
        findings=findings,
        filter_summary={"decision": "allow", "active_filters": 4},
        sandbox_summary={"total_runs": 2, "failures": 0},
        telemetry={"total_duration_ms": 1500, "files_scanned": 3},
        human_review_items=[f for f in findings if f.confidence < 0.5],
        recommendations=build_recommendations(findings),
    )


class TestJSONReport:
    """JSON report generation."""

    def test_generates_valid_json(self):
        report = _make_sample_report()
        output = generate_json_report(report)
        # Must be valid JSON
        data = json.loads(output)
        assert data["task_id"] == "test-report-001"

    def test_contains_all_sections(self):
        report = _make_sample_report()
        output = generate_json_report(report)
        data = json.loads(output)
        assert "summary" in data
        assert "filter_summary" in data
        assert "sandbox_summary" in data
        assert "telemetry" in data
        assert "findings" in data
        assert "human_review_items" in data
        assert "recommendations" in data

    def test_summary_counts_correct(self):
        report = _make_sample_report(2)
        output = generate_json_report(report)
        data = json.loads(output)
        assert data["summary"]["total_findings"] == 2

    def test_finding_fields_present(self):
        report = _make_sample_report(1)
        output = generate_json_report(report)
        data = json.loads(output)
        f = data["findings"][0]
        for key in ["severity", "category", "file", "line", "title",
                     "evidence", "recommendation", "confidence", "source"]:
            assert key in f, f"Missing finding field: {key}"


class TestMDReport:
    """Markdown report generation."""

    def test_contains_task_id(self):
        report = _make_sample_report()
        md = generate_md_report(report)
        assert "test-report-001" in md

    def test_contains_findings_section(self):
        report = _make_sample_report()
        md = generate_md_report(report)
        assert "## Findings" in md
        assert "Command injection" in md

    def test_contains_human_review_section(self):
        report = _make_sample_report(3)  # has low-confidence finding
        md = generate_md_report(report)
        assert "Needs Human Review" in md

    def test_contains_recommendations(self):
        report = _make_sample_report()
        md = generate_md_report(report)
        assert "## Recommendations" in md

    def test_no_findings_message(self):
        report = ReviewReport(
            task_id="empty",
            findings=[],
            filter_summary={"decision": "allow"},
            sandbox_summary={},
            telemetry={},
            human_review_items=[],
            recommendations=[],
        )
        md = generate_md_report(report)
        assert "No issues detected" in md


class TestBuildRecommendations:
    """Recommendation generation."""

    def test_critical_triggers_urgent(self):
        findings = [
            Finding(Severity.CRITICAL, FindingCategory.SECURITY, "f.py", 1,
                    "Critical", "ev", "fix", 0.9, "s"),
        ]
        recs = build_recommendations(findings)
        assert any("critical" in r.lower() for r in recs)

    def test_no_findings_no_recs(self):
        recs = build_recommendations([])
        assert len(recs) == 0
