# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""SQLite implementation of the CrRepository interface.

Provides persistent storage for the code review pipeline using SQLite.
All 5 tables (review_tasks, sandbox_runs, findings, review_reports, filter_logs)
plus monitor_summary are created on first use.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from .cr_repository import CrRepository
from .models import (
    FilterLog,
    Finding,
    MonitorSummary,
    ReviewReport,
    ReviewTask,
    SandboxRun,
    TaskStatus,
)


def _row_to_task(row: dict[str, Any]) -> ReviewTask:
    """Convert a DB row dict to a ReviewTask model."""
    return ReviewTask(
        id=row["id"],
        input_type=row["input_type"],
        input_summary=row["input_summary"],
        status=TaskStatus(row["status"]),
        total_duration_ms=row["total_duration_ms"],
        finding_count=row["finding_count"],
        severity_distribution=row["severity_distribution"],
        error_message=row["error_message"],
    )


def _row_to_finding(row: dict[str, Any]) -> Finding:
    """Convert a DB row dict to a Finding model."""
    return Finding(
        id=row["id"],
        task_id=row["task_id"],
        severity=row["severity"],
        category=row["category"],
        file_path=row["file_path"],
        line_number=row["line_number"],
        title=row["title"],
        evidence=row["evidence"],
        recommendation=row["recommendation"],
        confidence=row["confidence"],
        source=row["source"],
        dedup_key=row["dedup_key"],
        is_duplicate=bool(row["is_duplicate"]),
        needs_human_review=bool(row["needs_human_review"]),
    )


def _row_to_sandbox_run(row: dict[str, Any]) -> SandboxRun:
    """Convert a DB row dict to a SandboxRun model."""
    return SandboxRun(
        id=row["id"],
        task_id=row["task_id"],
        script_name=row["script_name"],
        status=row["status"],
        duration_ms=row["duration_ms"],
        output_size_bytes=row["output_size_bytes"],
        exit_code=row["exit_code"],
        error_message=row["error_message"],
        intercept_reason=row["intercept_reason"],
    )


def _row_to_report(row: dict[str, Any]) -> ReviewReport:
    """Convert a DB row dict to a ReviewReport model."""
    return ReviewReport(
        id=row["id"],
        task_id=row["task_id"],
        report_type=row["report_type"],
        content=row["content"],
        summary=row["summary"],
        filter_intercept_summary=row["filter_intercept_summary"],
        monitoring_metrics=row["monitoring_metrics"],
        sandbox_exec_summary=row["sandbox_exec_summary"],
    )


def _row_to_filter_log(row: dict[str, Any]) -> FilterLog:
    """Convert a DB row dict to a FilterLog model."""
    return FilterLog(
        id=row["id"],
        task_id=row["task_id"],
        filter_type=row["filter_type"],
        action=row["action"],
        target=row["target"],
        reason=row["reason"],
    )


def _row_to_monitor(row: dict[str, Any]) -> MonitorSummary:
    """Convert a DB row dict to a MonitorSummary model."""
    return MonitorSummary(
        id=row["id"],
        task_id=row["task_id"],
        total_duration_ms=row["total_duration_ms"],
        sandbox_duration_ms=row["sandbox_duration_ms"],
        tool_call_count=row["tool_call_count"],
        intercept_count=row["intercept_count"],
        finding_count=row["finding_count"],
        severity_distribution=row["severity_distribution"],
        exception_types=row["exception_types"],
        filter_intercepts=row["filter_intercepts"],
    )


class SqliteCrRepository(CrRepository):
    """SQLite-backed implementation of CrRepository.

    Uses the 'with' context manager pattern. Auto-creates all tables
    on first connection if they don't exist.

    Usage:
        repo = SqliteCrRepository("review.db")
        repo.create_task(task)
        ...
        repo.close()
    """

    def __init__(self, db_path: str, auto_init: bool = True) -> None:
        self._db_path = str(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        if auto_init:
            self._ensure_connection()
            self._init_tables()

    def _ensure_connection(self) -> sqlite3.Connection:
        """Get or create the SQLite connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    @property
    def conn(self) -> sqlite3.Connection:
        return self._ensure_connection()

    def _init_tables(self) -> None:
        """Create all tables from schema.sql if they don't exist."""
        schema_path = Path(__file__).parent / "schema.sql"
        if schema_path.exists():
            sql = schema_path.read_text(encoding="utf-8")
            self.conn.executescript(sql)
            self.conn.commit()

    # ── Review Tasks ──

    def create_task(self, task: ReviewTask) -> ReviewTask:
        self.conn.execute(
            """INSERT INTO review_tasks
               (id, input_type, input_summary, status, total_duration_ms,
                finding_count, severity_distribution, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.id, task.input_type, task.input_summary,
                task.status.value, task.total_duration_ms,
                task.finding_count, task.severity_distribution,
                task.error_message,
            ),
        )
        self.conn.commit()
        return task

    def get_task(self, task_id: str) -> Optional[ReviewTask]:
        row = self.conn.execute(
            "SELECT * FROM review_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return _row_to_task(dict(row)) if row else None

    def update_task(self, task: ReviewTask) -> None:
        self.conn.execute(
            """UPDATE review_tasks SET
               status=?, total_duration_ms=?, finding_count=?,
               severity_distribution=?, error_message=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (
                task.status.value, task.total_duration_ms,
                task.finding_count, task.severity_distribution,
                task.error_message, task.id,
            ),
        )
        self.conn.commit()

    def list_tasks(self, limit: int = 20, offset: int = 0) -> list[ReviewTask]:
        rows = self.conn.execute(
            "SELECT * FROM review_tasks ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [_row_to_task(dict(r)) for r in rows]

    # ── Findings ──

    def create_finding(self, finding: Finding) -> Finding:
        self.conn.execute(
            """INSERT INTO findings
               (id, task_id, severity, category, file_path, line_number,
                title, evidence, recommendation, confidence, source,
                dedup_key, is_duplicate, needs_human_review)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                finding.id, finding.task_id, finding.severity.value,
                finding.category.value, finding.file_path, finding.line_number,
                finding.title, finding.evidence, finding.recommendation,
                finding.confidence.value, finding.source.value,
                finding.dedup_key, int(finding.is_duplicate),
                int(finding.needs_human_review),
            ),
        )
        self.conn.commit()
        return finding

    def get_findings_by_task(self, task_id: str) -> list[Finding]:
        rows = self.conn.execute(
            "SELECT * FROM findings WHERE task_id = ? ORDER BY line_number",
            (task_id,),
        ).fetchall()
        return [_row_to_finding(dict(r)) for r in rows]

    def is_duplicate_finding(self, dedup_key: str, task_id: str) -> bool:
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM findings WHERE dedup_key = ? AND task_id = ?",
            (dedup_key, task_id),
        ).fetchone()
        return row["cnt"] > 0 if row else False

    def count_findings_by_task(self, task_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM findings WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return row["cnt"] if row else 0

    # ── Sandbox Runs ──

    def create_sandbox_run(self, run: SandboxRun) -> SandboxRun:
        self.conn.execute(
            """INSERT INTO sandbox_runs
               (id, task_id, script_name, status, duration_ms,
                output_size_bytes, exit_code, error_message, intercept_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run.id, run.task_id, run.script_name, run.status.value,
                run.duration_ms, run.output_size_bytes, run.exit_code,
                run.error_message, run.intercept_reason,
            ),
        )
        self.conn.commit()
        return run

    def get_sandbox_runs_by_task(self, task_id: str) -> list[SandboxRun]:
        rows = self.conn.execute(
            "SELECT * FROM sandbox_runs WHERE task_id = ? ORDER BY created_at",
            (task_id,),
        ).fetchall()
        return [_row_to_sandbox_run(dict(r)) for r in rows]

    # ── Reports ──

    def create_report(self, report: ReviewReport) -> ReviewReport:
        self.conn.execute(
            """INSERT INTO review_reports
               (id, task_id, report_type, content, summary,
                filter_intercept_summary, monitoring_metrics, sandbox_exec_summary)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                report.id, report.task_id, report.report_type.value,
                report.content, report.summary,
                report.filter_intercept_summary, report.monitoring_metrics,
                report.sandbox_exec_summary,
            ),
        )
        self.conn.commit()
        return report

    def get_reports_by_task(self, task_id: str) -> list[ReviewReport]:
        rows = self.conn.execute(
            "SELECT * FROM review_reports WHERE task_id = ? ORDER BY created_at",
            (task_id,),
        ).fetchall()
        return [_row_to_report(dict(r)) for r in rows]

    # ── Filter Logs ──

    def create_filter_log(self, log: FilterLog) -> FilterLog:
        self.conn.execute(
            """INSERT INTO filter_logs
               (id, task_id, filter_type, action, target, reason)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                log.id, log.task_id, log.filter_type.value,
                log.action.value, log.target, log.reason,
            ),
        )
        self.conn.commit()
        return log

    def get_filter_logs_by_task(self, task_id: str) -> list[FilterLog]:
        rows = self.conn.execute(
            "SELECT * FROM filter_logs WHERE task_id = ? ORDER BY created_at",
            (task_id,),
        ).fetchall()
        return [_row_to_filter_log(dict(r)) for r in rows]

    # ── Monitor Summary ──

    def create_monitor_summary(self, summary: MonitorSummary) -> MonitorSummary:
        self.conn.execute(
            """INSERT INTO monitor_summary
               (id, task_id, total_duration_ms, sandbox_duration_ms,
                tool_call_count, intercept_count, finding_count,
                severity_distribution, exception_types, filter_intercepts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                summary.id, summary.task_id, summary.total_duration_ms,
                summary.sandbox_duration_ms, summary.tool_call_count,
                summary.intercept_count, summary.finding_count,
                summary.severity_distribution, summary.exception_types,
                summary.filter_intercepts,
            ),
        )
        self.conn.commit()
        return summary

    def get_monitor_summary(self, task_id: str) -> Optional[MonitorSummary]:
        row = self.conn.execute(
            "SELECT * FROM monitor_summary WHERE task_id = ?", (task_id,)
        ).fetchone()
        return _row_to_monitor(dict(row)) if row else None

    # ── Lifecycle ──

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None