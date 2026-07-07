"""Data Access Object — SQLite CRUD operations for review data."""

import sqlite3
import os
from typing import Optional

from .models import FilterLogRecord, FindingRecord, ReviewTaskRecord, SandboxRunRecord

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


class ReviewDatabase:
    """SQLite-backed storage for code review results."""

    def __init__(self, db_path: str = "review_history.db"):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    # ── Connection management ──────────────────────────────────

    def connect(self) -> sqlite3.Connection:
        """Open connection and initialize schema if needed."""
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _init_schema(self) -> None:
        """Run DDL to create tables if they don't exist."""
        try:
            with open(_SCHEMA_PATH, "r") as f:
                self._conn.executescript(f.read())
        except FileNotFoundError:
            # Fallback: inline schema
            self._conn.executescript(_INLINE_SCHEMA)
        self._conn.commit()

    # ── Task operations ────────────────────────────────────────

    def insert_task(self, task: ReviewTaskRecord) -> None:
        """Insert a new review task."""
        self._conn.execute(
            """INSERT INTO review_tasks
               (task_id, diff_source, diff_summary, status, files_changed,
                total_findings, critical_count, high_count, medium_count,
                low_count, info_count, sandbox_runs, filter_intercepts,
                duration_ms, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (task.task_id, task.diff_source, task.diff_summary, task.status,
             task.files_changed, task.total_findings, task.critical_count,
             task.high_count, task.medium_count, task.low_count,
             task.info_count, task.sandbox_runs, task.filter_intercepts,
             task.duration_ms, task.created_at),
        )
        self._conn.commit()

    def get_task(self, task_id: str) -> ReviewTaskRecord | None:
        """Retrieve a task by ID."""
        row = self._conn.execute(
            "SELECT * FROM review_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        cols = [c[0] for c in self._conn.execute(
            "SELECT * FROM review_tasks LIMIT 0").description]
        d = dict(zip(cols, row))
        return ReviewTaskRecord(**d)

    # ── Finding operations ─────────────────────────────────────

    def insert_finding(self, finding: FindingRecord) -> int:
        """Insert a finding and return its ID."""
        cur = self._conn.execute(
            """INSERT INTO findings
               (task_id, severity, category, file, line, title,
                evidence, recommendation, confidence, source)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (finding.task_id, finding.severity, finding.category,
             finding.file, finding.line, finding.title,
             finding.evidence, finding.recommendation,
             finding.confidence, finding.source),
        )
        self._conn.commit()
        return cur.lastrowid

    def insert_findings_batch(self, findings: list[FindingRecord]) -> int:
        """Insert multiple findings efficiently. Returns count inserted."""
        data = [
            (f.task_id, f.severity, f.category, f.file, f.line,
             f.title, f.evidence, f.recommendation, f.confidence, f.source)
            for f in findings
        ]
        self._conn.executemany(
            """INSERT INTO findings
               (task_id, severity, category, file, line, title,
                evidence, recommendation, confidence, source)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            data,
        )
        self._conn.commit()
        return len(data)

    def get_findings_by_task(self, task_id: str) -> list[dict]:
        """Get all findings for a task."""
        rows = self._conn.execute(
            "SELECT severity, category, file, line, title, evidence, "
            "recommendation, confidence, source FROM findings "
            "WHERE task_id = ? ORDER BY "
            "CASE severity "
            "WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
            "WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END",
            (task_id,),
        ).fetchall()
        cols = ["severity", "category", "file", "line", "title",
                "evidence", "recommendation", "confidence", "source"]
        return [dict(zip(cols, r)) for r in rows]

    # ── Sandbox run operations ─────────────────────────────────

    def insert_sandbox_run(self, run: SandboxRunRecord) -> int:
        """Insert a sandbox run record."""
        cur = self._conn.execute(
            """INSERT INTO sandbox_runs
               (task_id, command, exit_code, stdout, stderr,
                duration_ms, timed_out, output_truncated, error)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (run.task_id, run.command, run.exit_code, run.stdout,
             run.stderr, run.duration_ms, int(run.timed_out),
             int(run.output_truncated), run.error),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_sandbox_runs_by_task(self, task_id: str) -> list[dict]:
        """Get all sandbox runs for a task."""
        rows = self._conn.execute(
            "SELECT command, exit_code, duration_ms, timed_out, "
            "output_truncated, error FROM sandbox_runs WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        cols = ["command", "exit_code", "duration_ms", "timed_out",
                "output_truncated", "error"]
        return [dict(zip(cols, r)) for r in rows]

    # ── Filter log operations ──────────────────────────────────

    def insert_filter_log(self, log: FilterLogRecord) -> int:
        """Insert a filter intercept log."""
        cur = self._conn.execute(
            """INSERT INTO filter_logs (task_id, action, reason, filter_name)
               VALUES (?,?,?,?)""",
            (log.task_id, log.action, log.reason, log.filter_name),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_filter_logs_by_task(self, task_id: str) -> list[dict]:
        """Get all filter logs for a task."""
        rows = self._conn.execute(
            "SELECT action, reason, filter_name FROM filter_logs "
            "WHERE task_id = ?", (task_id,),
        ).fetchall()
        cols = ["action", "reason", "filter_name"]
        return [dict(zip(cols, r)) for r in rows]

    # ── Bulk query ─────────────────────────────────────────────

    def get_task_full_report(self, task_id: str) -> dict:
        """Get a complete task report with all related records."""
        task = self.get_task(task_id)
        if task is None:
            return {"error": f"Task {task_id} not found"}

        return {
            "task": {
                "task_id": task.task_id,
                "diff_source": task.diff_source,
                "diff_summary": task.diff_summary,
                "status": task.status,
                "files_changed": task.files_changed,
                "findings_summary": {
                    "total": task.total_findings,
                    "critical": task.critical_count,
                    "high": task.high_count,
                    "medium": task.medium_count,
                    "low": task.low_count,
                    "info": task.info_count,
                },
                "sandbox_runs": task.sandbox_runs,
                "filter_intercepts": task.filter_intercepts,
                "duration_ms": task.duration_ms,
                "created_at": task.created_at,
            },
            "findings": self.get_findings_by_task(task_id),
            "sandbox_runs": self.get_sandbox_runs_by_task(task_id),
            "filter_logs": self.get_filter_logs_by_task(task_id),
        }


# Inline fallback schema (same as schema.sql)
_INLINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_tasks (
    task_id TEXT PRIMARY KEY,
    diff_source TEXT NOT NULL,
    diff_summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    files_changed INTEGER NOT NULL DEFAULT 0,
    total_findings INTEGER NOT NULL DEFAULT 0,
    critical_count INTEGER NOT NULL DEFAULT 0,
    high_count INTEGER NOT NULL DEFAULT 0,
    medium_count INTEGER NOT NULL DEFAULT 0,
    low_count INTEGER NOT NULL DEFAULT 0,
    info_count INTEGER NOT NULL DEFAULT 0,
    sandbox_runs INTEGER NOT NULL DEFAULT 0,
    filter_intercepts INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    file TEXT NOT NULL,
    line INTEGER NOT NULL,
    title TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '',
    recommendation TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.0,
    source TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES review_tasks(task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sandbox_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    command TEXT NOT NULL,
    exit_code INTEGER NOT NULL,
    stdout TEXT NOT NULL DEFAULT '',
    stderr TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    timed_out INTEGER NOT NULL DEFAULT 0,
    output_truncated INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES review_tasks(task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS filter_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    filter_name TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES review_tasks(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_findings_task ON findings(task_id);
CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(task_id, severity);
CREATE INDEX IF NOT EXISTS idx_sandbox_task ON sandbox_runs(task_id);
CREATE INDEX IF NOT EXISTS idx_filter_task ON filter_logs(task_id);
"""
