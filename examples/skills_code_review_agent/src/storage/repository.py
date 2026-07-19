"""Repository helpers built on top of SQLite for the review example."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from ..review_types import ReviewReport, ReviewTask
from .init_db import initialize_database


class ReviewRepository:
    """Small SQLite repository for review tasks and related records."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = initialize_database(db_path)

    def save_review(
        self,
        *,
        task: ReviewTask,
        report: ReviewReport,
        report_json: dict[str, object],
        report_markdown: str,
        runtime_type: str,
        dry_run: bool,
        fake_model: bool,
        created_at: str,
        finished_at: str,
        total_duration_ms: int,
    ) -> None:
        """Persist a review task and all related structured records."""

        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT OR REPLACE INTO review_tasks (
                    task_id, status, input_type, runtime_type, dry_run, fake_model,
                    source, created_at, finished_at, total_duration_ms, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.task_id,
                    task.status.value,
                    task.review_input.kind.value,
                    runtime_type,
                    int(dry_run),
                    int(fake_model),
                    task.review_input.source,
                    created_at,
                    finished_at,
                    total_duration_ms,
                    task.error_message,
                ),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO review_inputs (
                    task_id, diff_sha256, changed_files_count, hunk_count,
                    candidate_line_count, input_summary
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task.task_id,
                    hashlib.sha256(task.review_input.diff_text.encode("utf-8")).hexdigest(),
                    task.parsed_diff.changed_files_count if task.parsed_diff else 0,
                    _count_hunks(task),
                    _count_candidate_lines(task),
                    _build_input_summary(task),
                ),
            )

            connection.execute("DELETE FROM filter_decisions WHERE task_id = ?", (task.task_id,))
            connection.executemany(
                """
                INSERT INTO filter_decisions (
                    task_id, decision, reason_code, reason_text, target, requires_human_review
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        task.task_id,
                        decision.decision.value,
                        decision.reason_code,
                        decision.reason,
                        decision.target,
                        int(decision.requires_human_review),
                    )
                    for decision in task.filter_decisions
                ],
            )

            connection.execute("DELETE FROM sandbox_runs WHERE task_id = ?", (task.task_id,))
            connection.executemany(
                """
                INSERT INTO sandbox_runs (
                    task_id, run_name, command, status, runtime, duration_ms, exit_code,
                    stdout_summary, stderr_summary, timed_out, output_truncated, blocked_by_filter
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        task.task_id,
                        sandbox_run.name,
                        json.dumps(sandbox_run.command),
                        sandbox_run.status.value,
                        sandbox_run.runtime,
                        sandbox_run.duration_ms,
                        sandbox_run.exit_code,
                        sandbox_run.stdout,
                        sandbox_run.stderr,
                        int(sandbox_run.timed_out),
                        int(sandbox_run.output_truncated),
                        int(sandbox_run.blocked_by_filter),
                    )
                    for sandbox_run in task.sandbox_runs
                ],
            )

            connection.execute("DELETE FROM findings WHERE task_id = ?", (task.task_id,))
            connection.executemany(
                """
                INSERT INTO findings (
                    task_id, fingerprint, severity, category, file, line, title,
                    evidence, recommendation, confidence, source, disposition
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        task.task_id,
                        finding.fingerprint,
                        finding.severity.value,
                        finding.category.value,
                        finding.file,
                        finding.line,
                        finding.title,
                        finding.evidence,
                        finding.recommendation,
                        finding.confidence,
                        finding.source.value,
                        finding.disposition.value,
                    )
                    for finding in task.findings
                ],
            )

            connection.execute(
                """
                INSERT OR REPLACE INTO review_reports (
                    task_id, final_verdict, report_json, report_markdown, monitoring_summary
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    task.task_id,
                    report.conclusion.value,
                    json.dumps(report_json, ensure_ascii=False),
                    report_markdown,
                    json.dumps(report.monitoring_summary, ensure_ascii=False),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def get_review_bundle(self, task_id: str) -> dict[str, object]:
        """Fetch the full persisted record set for a task id."""

        connection = self._connect()
        try:
            task_row = connection.execute(
                "SELECT * FROM review_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if task_row is None:
                raise KeyError(f"task not found: {task_id}")

            return {
                "task": dict(task_row),
                "input": _fetch_one(connection, "review_inputs", task_id),
                "filter_decisions": _fetch_many(connection, "filter_decisions", task_id),
                "sandbox_runs": _fetch_many(connection, "sandbox_runs", task_id),
                "findings": _fetch_many(connection, "findings", task_id),
                "report": _fetch_one(connection, "review_reports", task_id),
            }
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        """Open a SQLite connection with row access by column name."""

        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection


def _fetch_one(
    connection: sqlite3.Connection,
    table_name: str,
    task_id: str,
) -> dict[str, object] | None:
    """Fetch a single row by task id."""

    row = connection.execute(
        f"SELECT * FROM {table_name} WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _fetch_many(
    connection: sqlite3.Connection,
    table_name: str,
    task_id: str,
) -> list[dict[str, object]]:
    """Fetch all rows by task id."""

    rows = connection.execute(
        f"SELECT * FROM {table_name} WHERE task_id = ?",
        (task_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _count_hunks(task: ReviewTask) -> int:
    """Count all parsed hunks for a task."""

    if task.parsed_diff is None:
        return 0
    return sum(len(changed_file.hunks) for changed_file in task.parsed_diff.files)


def _count_candidate_lines(task: ReviewTask) -> int:
    """Count all candidate review lines across files."""

    if task.parsed_diff is None:
        return 0
    return sum(
        len(changed_file.candidate_line_numbers())
        for changed_file in task.parsed_diff.files
    )


def _build_input_summary(task: ReviewTask) -> str:
    """Build a short input summary suitable for storage and debugging."""

    if task.parsed_diff is None:
        return "No parsed diff available."
    changed_paths = ", ".join(task.parsed_diff.changed_paths[:5])
    return (
        f"input_kind={task.review_input.kind.value}; "
        f"changed_files={task.parsed_diff.changed_files_count}; "
        f"paths={changed_paths}"
    )
