"""Tests for SQL-backed task, finding, and report persistence."""

from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import timezone
import threading

import pytest

from examples.skills_code_review_agent.agent.input_parser import parse_diff_text
from examples.skills_code_review_agent.agent.models import Category
from examples.skills_code_review_agent.agent.models import Finding
from examples.skills_code_review_agent.agent.models import ReviewMetrics
from examples.skills_code_review_agent.agent.models import ReviewReport
from examples.skills_code_review_agent.agent.models import Severity
from examples.skills_code_review_agent.agent.models import TaskStatus
from examples.skills_code_review_agent.agent.policy import SecretRedactor
from examples.skills_code_review_agent.agent.storage import ReviewStore

DIFF = """--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-safe(value)
+eval(value)
"""


def _finding(**updates) -> Finding:
    values = {
        "severity": Severity.MEDIUM,
        "category": Category.SECURITY,
        "file": "app.py",
        "line": 1,
        "title": "Unsafe eval",
        "evidence": "eval(value)",
        "recommendation": "Use a parser.",
        "confidence": 0.8,
        "source": "rule.one",
    }
    values.update(updates)
    return Finding.model_validate(values)


def _report(task_id: str, findings: list[Finding]) -> ReviewReport:
    return ReviewReport(
        task_id=task_id,
        status=TaskStatus.COMPLETE,
        input_summary={"file_count": 1},
        findings=findings,
        warnings=[],
        needs_human_review=[],
        filter_decisions=[],
        sandbox_runs=[],
        failures=[],
        metrics=ReviewMetrics(
            total_duration_ms=5,
            sandbox_duration_ms=2,
            tool_calls=2,
            blocked_count=0,
            finding_count=len(findings),
        ),
        conclusion="Review complete.",
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
async def store(tmp_path):
    database = tmp_path / "review.db"
    value = ReviewStore(f"sqlite:///{database.as_posix()}", SecretRedactor())
    await value.initialize()
    yield value
    await value.close()


@pytest.mark.asyncio
async def test_task_findings_and_report_round_trip(store) -> None:
    task_id = "round-trip"
    review_input = parse_diff_text(DIFF, "test")
    finding = _finding()
    await store.create_task(task_id, review_input)
    await store.save_findings(task_id, [finding], set())
    report = _report(task_id, [finding])
    await store.complete_task(report, "markdown")

    stored = await store.get_report(task_id)
    findings = await store.list_findings(task_id)
    assert stored["status"] == TaskStatus.COMPLETE.value
    assert stored["report"]["findings"][0]["title"] == "Unsafe eval"
    assert stored["markdown"] == "markdown"
    assert len(findings) == 1


@pytest.mark.asyncio
async def test_duplicate_finding_is_merged(store) -> None:
    task_id = "deduplicate"
    await store.create_task(task_id, parse_diff_text(DIFF, "test"))
    original = _finding(source="rule.one", evidence="first")
    stronger = _finding(
        severity=Severity.HIGH,
        confidence=0.95,
        source="rule.two",
        evidence="second",
    )
    barrier = threading.Barrier(2)

    def write(finding):
        barrier.wait()
        asyncio.run(store.save_findings(task_id, [finding], set()))

    await asyncio.gather(
        asyncio.to_thread(write, original),
        asyncio.to_thread(write, stronger),
    )
    findings = await store.list_findings(task_id)
    assert len(findings) == 1
    assert findings[0].severity == Severity.HIGH.value
    assert findings[0].confidence == 0.95
    assert set(findings[0].source.split(" | ")) == {"rule.one", "rule.two"}


@pytest.mark.asyncio
async def test_unknown_task_status_raises(store) -> None:
    with pytest.raises(KeyError, match="unknown review task"):
        await store.update_status("missing", TaskStatus.FAILED, "token=secret")
    assert await store.get_report("missing") is None


@pytest.mark.asyncio
async def test_failure_reason_is_redacted(store) -> None:
    task_id = "failure"
    await store.create_task(task_id, parse_diff_text(DIFF, "test"))
    secret = "sk-1234567890abcdefghijklmnop"
    await store.update_status(task_id, TaskStatus.FAILED, f"token={secret}")
    stored = await store.get_report(task_id)
    assert secret not in stored["failure_reason"]
    assert stored["status"] == TaskStatus.FAILED.value


@pytest.mark.asyncio
async def test_json_style_secret_is_redacted_from_finding_storage(store) -> None:
    task_id = "json-secret"
    secret = "CorrectHorseBatteryStaple"
    finding = _finding(evidence=f'"password": "{secret}"')
    await store.create_task(task_id, parse_diff_text(DIFF, "test"))

    await store.save_findings(task_id, [finding], set())

    findings = await store.list_findings(task_id)
    assert secret not in findings[0].evidence
