#!/usr/bin/env python3
"""Run the persistence contract against a real PostgreSQL database."""

import json
import os
import sys
import uuid
from datetime import datetime
from datetime import timezone
from pathlib import Path

EXAMPLE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EXAMPLE_ROOT))

from reports.models import ReviewReport
from storage.postgresql import PostgreSQLReviewStore


def main() -> int:
    dsn = os.getenv("CODE_REVIEW_POSTGRES_DSN", "")
    if not dsn:
        print("CODE_REVIEW_POSTGRES_DSN is required", file=sys.stderr)
        return 2

    store = PostgreSQLReviewStore(dsn)
    store.initialize()
    sample = ReviewReport.model_validate_json(
        (EXAMPLE_ROOT / "examples" / "review_report.json").read_text(
            encoding="utf-8"
        )
    )
    task_id = f"postgres-integration-{uuid.uuid4()}"
    marker = "sk-postgres-integration-fake-secret-1234567890"
    finding = sample.analysis.findings[0].model_copy(
        update={"evidence": f"api_key={marker}"}
    )
    analysis = sample.analysis.model_copy(update={"findings": [finding]})
    sandbox_runs = [
        item.model_copy(update={"run_id": f"{task_id}-run-{index}"})
        for index, item in enumerate(sample.sandbox_runs)
    ]
    filter_decisions = [
        item.model_copy(update={"decision_id": f"{task_id}-decision-{index}"})
        for index, item in enumerate(sample.filter_decisions)
    ]
    now = datetime.now(timezone.utc)
    report = sample.model_copy(
        update={
            "task_id": task_id,
            "created_at": now,
            "completed_at": now,
            "analysis": analysis,
            "sandbox_runs": sandbox_runs,
            "filter_decisions": filter_decisions,
            "conclusion": f"token={marker}",
        }
    )

    store.start_task(task_id, now, "postgres-integration", report.scope)
    store.save(report)
    store.save(report)

    loaded = store.get(task_id)
    assert loaded is not None
    assert loaded.task_id == task_id
    assert marker not in loaded.model_dump_json()
    assert "[REDACTED]" in loaded.model_dump_json()
    cached = store.get_latest_by_input_digest(
        report.input_summary.digest,
        report.input_summary.review_profile,
    )
    assert cached is not None
    assert cached.task_id == task_id

    details = store.get_task_details(task_id)
    assert details is not None
    assert details["task"]["status"] == report.status
    assert len(details["sandbox_runs"]) == len(report.sandbox_runs)
    assert len(details["filter_decisions"]) == len(report.filter_decisions)
    expected_findings = sum(
        len(items)
        for items in (
            report.analysis.findings,
            report.analysis.warnings,
            report.analysis.needs_human_review,
        )
    )
    assert len(details["findings"]) == expected_findings
    assert details["monitoring"] is not None
    assert details["report"] is not None
    assert marker not in json.dumps(details, default=str)

    failed_task_id = f"postgres-failed-{uuid.uuid4()}"
    store.start_task(failed_task_id, now, "postgres-integration", report.scope)
    store.mark_task_failed(failed_task_id, now, f"password={marker}")
    failed = store.get_task_details(failed_task_id)
    assert failed is not None
    assert failed["task"]["status"] == "failed"
    assert marker not in json.dumps(failed, default=str)

    print(
        json.dumps(
            {
                "postgresql_initialized": True,
                "task_round_trip": True,
                "idempotent_save": True,
                "normalized_details": True,
                "cache_query": True,
                "failure_audit": True,
                "redaction": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
