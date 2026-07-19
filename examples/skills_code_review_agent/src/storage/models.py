"""SQLite schema definitions for review tasks, runs, findings, and reports."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class TableSchema:
    """Simple SQLite table schema descriptor."""

    name: str
    ddl: str


REVIEW_TASKS_TABLE = TableSchema(
    name="review_tasks",
    ddl="""
    CREATE TABLE IF NOT EXISTS review_tasks (
        task_id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        input_type TEXT NOT NULL,
        runtime_type TEXT NOT NULL,
        dry_run INTEGER NOT NULL,
        fake_model INTEGER NOT NULL,
        source TEXT NOT NULL,
        created_at TEXT NOT NULL,
        finished_at TEXT NOT NULL,
        total_duration_ms INTEGER NOT NULL,
        error_message TEXT
    )
    """.strip(),
)

REVIEW_INPUTS_TABLE = TableSchema(
    name="review_inputs",
    ddl="""
    CREATE TABLE IF NOT EXISTS review_inputs (
        task_id TEXT PRIMARY KEY,
        diff_sha256 TEXT NOT NULL,
        changed_files_count INTEGER NOT NULL,
        hunk_count INTEGER NOT NULL,
        candidate_line_count INTEGER NOT NULL,
        input_summary TEXT NOT NULL,
        FOREIGN KEY (task_id) REFERENCES review_tasks(task_id)
    )
    """.strip(),
)

FILTER_DECISIONS_TABLE = TableSchema(
    name="filter_decisions",
    ddl="""
    CREATE TABLE IF NOT EXISTS filter_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        decision TEXT NOT NULL,
        reason_code TEXT NOT NULL,
        reason_text TEXT NOT NULL,
        target TEXT NOT NULL,
        requires_human_review INTEGER NOT NULL,
        FOREIGN KEY (task_id) REFERENCES review_tasks(task_id)
    )
    """.strip(),
)

SANDBOX_RUNS_TABLE = TableSchema(
    name="sandbox_runs",
    ddl="""
    CREATE TABLE IF NOT EXISTS sandbox_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        run_name TEXT NOT NULL,
        command TEXT NOT NULL,
        status TEXT NOT NULL,
        runtime TEXT NOT NULL,
        duration_ms INTEGER NOT NULL,
        exit_code INTEGER,
        stdout_summary TEXT NOT NULL,
        stderr_summary TEXT NOT NULL,
        timed_out INTEGER NOT NULL,
        output_truncated INTEGER NOT NULL,
        blocked_by_filter INTEGER NOT NULL,
        FOREIGN KEY (task_id) REFERENCES review_tasks(task_id)
    )
    """.strip(),
)

FINDINGS_TABLE = TableSchema(
    name="findings",
    ddl="""
    CREATE TABLE IF NOT EXISTS findings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        fingerprint TEXT,
        severity TEXT NOT NULL,
        category TEXT NOT NULL,
        file TEXT NOT NULL,
        line INTEGER,
        title TEXT NOT NULL,
        evidence TEXT NOT NULL,
        recommendation TEXT NOT NULL,
        confidence REAL NOT NULL,
        source TEXT NOT NULL,
        disposition TEXT NOT NULL,
        FOREIGN KEY (task_id) REFERENCES review_tasks(task_id)
    )
    """.strip(),
)

REVIEW_REPORTS_TABLE = TableSchema(
    name="review_reports",
    ddl="""
    CREATE TABLE IF NOT EXISTS review_reports (
        task_id TEXT PRIMARY KEY,
        final_verdict TEXT NOT NULL,
        report_json TEXT NOT NULL,
        report_markdown TEXT NOT NULL,
        monitoring_summary TEXT NOT NULL,
        FOREIGN KEY (task_id) REFERENCES review_tasks(task_id)
    )
    """.strip(),
)

ALL_TABLES = (
    REVIEW_TASKS_TABLE,
    REVIEW_INPUTS_TABLE,
    FILTER_DECISIONS_TABLE,
    SANDBOX_RUNS_TABLE,
    FINDINGS_TABLE,
    REVIEW_REPORTS_TABLE,
)
