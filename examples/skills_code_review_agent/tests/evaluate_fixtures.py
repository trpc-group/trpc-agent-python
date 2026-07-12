#!/usr/bin/env python3
"""Measure deterministic detector recall and clean-diff false positives."""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

EXAMPLE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EXAMPLE_ROOT))

from reports.writers import ReportWriter
from storage.sqlite import SQLiteReviewStore
from workflow import CodeReviewWorkflow
from workflow import ReviewRequest

EXPECTED_CATEGORIES = {
    "security": "security",
    "async-resource-leak": "async_error",
    "database-lifecycle": "database_lifecycle",
    "sensitive-redaction": "sensitive_information",
}
EXPECTED_SECRET_LINES = 11
REQUIRED_FIXTURES = (
    "clean",
    "security",
    "async-resource-leak",
    "database-lifecycle",
    "test-missing",
    "duplicate-finding",
    "sandbox-failure",
    "sensitive-redaction",
)


async def evaluate() -> dict[str, object]:
    """Run public fixtures through the same fake workflow used by acceptance tests."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        workflow = CodeReviewWorkflow(
            model_config=None,
            sandbox=None,
            store=SQLiteReviewStore(root / "reviews.sqlite3"),
            report_writer=ReportWriter(root / "reports"),
            skills_path=EXAMPLE_ROOT / "skills",
        )
        fixture_results = {}
        fixture_outputs = {}
        for fixture in REQUIRED_FIXTURES:
            result = await workflow.run(
                ReviewRequest(fixture=fixture, fake_model=True)
            )
            fixture_results[fixture] = result
            fixture_outputs[fixture] = {
                "status": result.report.status,
                "json_report": result.artifacts.json_path.is_file(),
                "markdown_report": result.artifacts.markdown_path.is_file(),
            }

        detected = 0
        details = {}
        for fixture, expected in EXPECTED_CATEGORIES.items():
            result = fixture_results[fixture]
            categories = {item.category for item in result.report.analysis.findings}
            matched = expected in categories
            detected += int(matched)
            details[fixture] = {
                "expected": expected,
                "categories": sorted(categories),
                "matched": matched,
            }

        clean = fixture_results["clean"]
        false_positive_count = len(clean.report.analysis.findings)
        total_positive = len(EXPECTED_CATEGORIES)
        secret_findings = [
            item
            for item in fixture_results[
                "sensitive-redaction"
            ].report.analysis.findings
            if item.category == "sensitive_information"
        ]
        return {
            "high_risk_detection_rate": detected / total_positive,
            "clean_false_positive_rate": float(false_positive_count > 0),
            "sensitive_redaction_detection_rate": min(
                len(secret_findings) / EXPECTED_SECRET_LINES,
                1.0,
            ),
            "sensitive_findings": len(secret_findings),
            "expected_sensitive_lines": EXPECTED_SECRET_LINES,
            "required_fixture_report_count": sum(
                int(item["json_report"] and item["markdown_report"])
                for item in fixture_outputs.values()
            ),
            "required_fixture_count": len(REQUIRED_FIXTURES),
            "fixture_outputs": fixture_outputs,
            "detected": detected,
            "expected": total_positive,
            "details": details,
        }


def main() -> int:
    result = asyncio.run(evaluate())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["high_risk_detection_rate"] < 0.80:
        return 1
    if result["clean_false_positive_rate"] > 0.15:
        return 1
    if result["sensitive_redaction_detection_rate"] < 0.95:
        return 1
    if result["required_fixture_report_count"] != result["required_fixture_count"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
