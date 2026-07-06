"""SQLite persistence for deterministic code review runs."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any


def init_db(db_path: Path) -> None:
    """Create the SQLite database and tables if they do not already exist."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS review_tasks (
                task_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                diff_file TEXT NOT NULL,
                dry_run INTEGER NOT NULL,
                files_scanned TEXT NOT NULL,
                total_findings INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                severity TEXT NOT NULL,
                category TEXT NOT NULL,
                file TEXT NOT NULL,
                line INTEGER NOT NULL,
                title TEXT NOT NULL,
                evidence TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                confidence REAL NOT NULL,
                source TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES review_tasks(task_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                task_id TEXT PRIMARY KEY,
                json_report_path TEXT NOT NULL,
                markdown_report_path TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES review_tasks(task_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sandbox_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                runner_name TEXT NOT NULL,
                timeout_seconds INTEGER NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                stdout_summary TEXT NOT NULL,
                stderr_summary TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES review_tasks(task_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS filter_decisions (
                task_id TEXT PRIMARY KEY,
                decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES review_tasks(task_id) ON DELETE CASCADE
            )
            """
        )


def persist_review(
    *,
    db_path: Path,
    report: dict[str, Any],
    json_report_path: Path,
    markdown_report_path: Path,
) -> str:
    """Persist one review task, its findings, and report metadata."""

    init_db(db_path)
    task_id = str(uuid.uuid4())
    summary = report["summary"]
    findings = report["findings"]
    sandbox = report.get("sandbox", {})
    filter_decision = report.get("filter", {})

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            INSERT INTO review_tasks (
                task_id,
                created_at,
                diff_file,
                dry_run,
                files_scanned,
                total_findings
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                summary["generated_at"],
                summary["diff_file"],
                1 if summary["dry_run"] else 0,
                json.dumps(summary["files_scanned"], ensure_ascii=False),
                int(summary["total_findings"]),
            ),
        )
        conn.executemany(
            """
            INSERT INTO findings (
                task_id,
                severity,
                category,
                file,
                line,
                title,
                evidence,
                recommendation,
                confidence,
                source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    task_id,
                    finding["severity"],
                    finding["category"],
                    finding["file"],
                    int(finding["line"]),
                    finding["title"],
                    finding["evidence"],
                    finding["recommendation"],
                    float(finding["confidence"]),
                    finding["source"],
                )
                for finding in findings
            ],
        )
        conn.execute(
            """
            INSERT INTO reports (
                task_id,
                json_report_path,
                markdown_report_path,
                summary_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                task_id,
                str(json_report_path),
                str(markdown_report_path),
                json.dumps(summary, ensure_ascii=False),
            ),
        )
        conn.execute(
            """
            INSERT INTO sandbox_runs (
                task_id,
                runner_name,
                timeout_seconds,
                status,
                started_at,
                finished_at,
                stdout_summary,
                stderr_summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                sandbox.get("runner_name", ""),
                int(sandbox.get("timeout_seconds", 0)),
                sandbox.get("status", ""),
                sandbox.get("started_at", ""),
                sandbox.get("finished_at", ""),
                sandbox.get("stdout_summary", ""),
                sandbox.get("stderr_summary", ""),
            ),
        )
        conn.execute(
            """
            INSERT INTO filter_decisions (
                task_id,
                decision,
                reason
            ) VALUES (?, ?, ?)
            """,
            (
                task_id,
                filter_decision.get("decision", ""),
                filter_decision.get("reason", ""),
            ),
        )

    return task_id
