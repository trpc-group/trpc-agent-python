# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""SQLite persistence for the code review dry-run example."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .filters import redact_text
from .report import render_markdown_report
from .schemas import AuditEvent
from .schemas import FilterDecision
from .schemas import ReviewFinding
from .schemas import ReviewInput
from .schemas import ReviewReport
from .schemas import ReviewTaskStatus
from .schemas import SandboxRun

_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_tasks (
    task_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    input_type TEXT,
    repo_path TEXT,
    diff_file TEXT,
    diff_sha256 TEXT,
    diff_summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    error_type TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS sandbox_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    runtime TEXT NOT NULL,
    script_name TEXT NOT NULL,
    decision TEXT NOT NULL,
    exit_code INTEGER,
    timed_out INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    stdout_excerpt TEXT NOT NULL,
    stderr_excerpt TEXT NOT NULL,
    output_truncated INTEGER NOT NULL,
    error_type TEXT,
    FOREIGN KEY(task_id) REFERENCES review_tasks(task_id)
);

CREATE TABLE IF NOT EXISTS filter_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    filter_name TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    file TEXT,
    line INTEGER,
    script_name TEXT,
    fingerprint TEXT,
    FOREIGN KEY(task_id) REFERENCES review_tasks(task_id)
);

CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    is_warning INTEGER NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    file TEXT NOT NULL,
    line INTEGER NOT NULL,
    title TEXT NOT NULL,
    evidence TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    confidence TEXT NOT NULL,
    source TEXT NOT NULL,
    fingerprint TEXT,
    finding_json TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES review_tasks(task_id)
);

CREATE TABLE IF NOT EXISTS review_reports (
    task_id TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    report_json TEXT NOT NULL,
    report_markdown TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES review_tasks(task_id)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES review_tasks(task_id)
);
"""


class ReviewStorage:
    """Small SQLite storage adapter for review reports."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.db_path)
        self._connection.row_factory = sqlite3.Row
        self.init_schema()

    def close(self) -> None:
        """Close the SQLite connection."""
        self._connection.close()

    def init_schema(self) -> None:
        """Initialize storage schema."""
        self._connection.executescript(_SCHEMA)
        self._connection.commit()

    def create_task(self, *, task_id: str, status: ReviewTaskStatus, mode: str, review_input: ReviewInput | None) -> str:
        """Create a review task row."""
        payload = review_input.model_dump(mode="json") if review_input else {}
        self._connection.execute(
            """
            INSERT OR REPLACE INTO review_tasks (
                task_id, status, mode, input_type, repo_path, diff_file, diff_sha256, diff_summary_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                status.value,
                mode,
                payload.get("input_type"),
                payload.get("repo_path"),
                payload.get("diff_file"),
                payload.get("diff_sha256"),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        self._connection.commit()
        return task_id

    def update_task_status(
        self,
        task_id: str,
        status: ReviewTaskStatus,
        *,
        duration_ms: int = 0,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update task status."""
        self._connection.execute(
            """
            UPDATE review_tasks
            SET status = ?, duration_ms = ?, error_type = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
            WHERE task_id = ?
            """,
            (status.value, duration_ms, error_type, redact_text(error_message or "") or None, task_id),
        )
        self._connection.commit()

    def record_sandbox_runs(self, task_id: str, runs: list[SandboxRun]) -> None:
        """Persist sandbox run records."""
        self._connection.executemany(
            """
            INSERT INTO sandbox_runs (
                task_id, runtime, script_name, decision, exit_code, timed_out, duration_ms,
                stdout_excerpt, stderr_excerpt, output_truncated, error_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    task_id,
                    run.runtime,
                    run.script_name,
                    run.decision,
                    run.exit_code,
                    int(run.timed_out),
                    run.duration_ms,
                    redact_text(run.stdout_excerpt),
                    redact_text(run.stderr_excerpt),
                    int(run.output_truncated),
                    run.error_type,
                )
                for run in runs
            ],
        )
        self._connection.commit()

    def record_filter_decisions(self, task_id: str, decisions: list[FilterDecision]) -> None:
        """Persist filter decisions."""
        self._connection.executemany(
            """
            INSERT INTO filter_decisions (
                task_id, stage, filter_name, decision, reason, file, line, script_name, fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    task_id,
                    decision.stage,
                    decision.filter_name,
                    decision.decision,
                    redact_text(decision.reason),
                    decision.file,
                    decision.line,
                    decision.script_name,
                    decision.fingerprint,
                )
                for decision in decisions
            ],
        )
        self._connection.commit()

    def record_findings(self, task_id: str, findings: list[ReviewFinding], *, is_warning: bool) -> None:
        """Persist findings or warnings."""
        self._connection.executemany(
            """
            INSERT INTO findings (
                task_id, is_warning, severity, category, file, line, title, evidence,
                recommendation, confidence, source, fingerprint, finding_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [self._finding_row(task_id, finding, is_warning=is_warning) for finding in findings],
        )
        self._connection.commit()

    def record_audit_events(self, task_id: str, events: list[AuditEvent]) -> None:
        """Persist audit events."""
        self._connection.executemany(
            """
            INSERT INTO audit_events (task_id, event_type, severity, message, details_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    task_id,
                    event.event_type,
                    event.severity,
                    redact_text(event.message),
                    json.dumps(event.details, ensure_ascii=False),
                )
                for event in events
            ],
        )
        self._connection.commit()

    def record_report(self, task_id: str, report: ReviewReport) -> None:
        """Persist final JSON and Markdown report."""
        self._connection.execute(
            """
            INSERT OR REPLACE INTO review_reports (task_id, summary, metrics_json, report_json, report_markdown)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                task_id,
                report.summary,
                json.dumps(report.metrics.model_dump(mode="json"), ensure_ascii=False),
                report.model_dump_json(indent=2),
                render_markdown_report(report),
            ),
        )
        self._connection.commit()

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Return a queryable task summary by task id."""
        task = self._connection.execute("SELECT * FROM review_tasks WHERE task_id = ?", (task_id,)).fetchone()
        if task is None:
            return None
        report = self._connection.execute("SELECT * FROM review_reports WHERE task_id = ?", (task_id,)).fetchone()
        return {
            "task": dict(task),
            "sandbox_runs": self._rows("SELECT * FROM sandbox_runs WHERE task_id = ?", task_id),
            "filter_decisions": self._rows("SELECT * FROM filter_decisions WHERE task_id = ?", task_id),
            "findings": self._rows("SELECT * FROM findings WHERE task_id = ?", task_id),
            "audit_events": self._rows("SELECT * FROM audit_events WHERE task_id = ?", task_id),
            "report": dict(report) if report else None,
        }

    def _rows(self, query: str, task_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self._connection.execute(query, (task_id,)).fetchall()]

    def _finding_row(self, task_id: str, finding: ReviewFinding, *, is_warning: bool) -> tuple[Any, ...]:
        payload = finding.model_dump(mode="json")
        return (
            task_id,
            int(is_warning),
            finding.severity.value,
            finding.category,
            finding.file,
            finding.line,
            finding.title,
            finding.evidence,
            finding.recommendation,
            finding.confidence.value,
            finding.source.value,
            finding.fingerprint,
            json.dumps(payload, ensure_ascii=False),
        )
