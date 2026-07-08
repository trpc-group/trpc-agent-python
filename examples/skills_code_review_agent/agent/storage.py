"""SQLite persistence for review tasks, sandbox runs, findings, and reports."""

from __future__ import annotations

import json
import sqlite3
from urllib.parse import unquote
from urllib.parse import urlparse
from abc import ABC
from abc import abstractmethod
from pathlib import Path
from typing import Any

from .models import FilterDecision
from .models import Finding
from .models import MonitoringSummary
from .models import ReviewReport
from .models import SandboxRun

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_tasks (
    task_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    conclusion TEXT
);
CREATE TABLE IF NOT EXISTS input_diffs (
    task_id TEXT PRIMARY KEY,
    diff_summary_json TEXT NOT NULL,
    diff_text TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES review_tasks(task_id)
);
CREATE TABLE IF NOT EXISTS sandbox_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    name TEXT NOT NULL,
    runtime TEXT NOT NULL,
    command TEXT NOT NULL,
    status TEXT NOT NULL,
    exit_code INTEGER,
    duration_ms INTEGER NOT NULL,
    stdout TEXT NOT NULL,
    stderr TEXT NOT NULL,
    timed_out INTEGER NOT NULL,
    output_truncated INTEGER NOT NULL,
    exception_type TEXT,
    FOREIGN KEY(task_id) REFERENCES review_tasks(task_id)
);
CREATE TABLE IF NOT EXISTS filter_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    command TEXT NOT NULL,
    path TEXT NOT NULL,
    policy TEXT NOT NULL,
    severity TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES review_tasks(task_id)
);
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    bucket TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    file TEXT NOT NULL,
    line INTEGER NOT NULL,
    title TEXT NOT NULL,
    evidence TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    confidence REAL NOT NULL,
    source TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    hunk_header TEXT NOT NULL,
    context_before_json TEXT NOT NULL,
    context_after_json TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES review_tasks(task_id)
);
CREATE TABLE IF NOT EXISTS monitoring_summaries (
    task_id TEXT PRIMARY KEY,
    summary_json TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES review_tasks(task_id)
);
CREATE TABLE IF NOT EXISTS review_reports (
    task_id TEXT PRIMARY KEY,
    report_json TEXT NOT NULL,
    report_markdown TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES review_tasks(task_id)
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_review_tasks_status_created ON review_tasks(status, created_at);
CREATE INDEX IF NOT EXISTS idx_sandbox_runs_task_status ON sandbox_runs(task_id, status);
CREATE INDEX IF NOT EXISTS idx_filter_decisions_task_decision ON filter_decisions(task_id, decision);
CREATE INDEX IF NOT EXISTS idx_findings_task_bucket ON findings(task_id, bucket);
CREATE INDEX IF NOT EXISTS idx_findings_task_severity ON findings(task_id, severity);
CREATE INDEX IF NOT EXISTS idx_findings_task_file_line_category ON findings(task_id, file, line, category);
"""

COLUMN_MIGRATIONS: dict[str, dict[str, str]] = {
    "review_tasks": {
        "completed_at": "TEXT",
        "conclusion": "TEXT",
    },
    "sandbox_runs": {
        "timed_out": "INTEGER NOT NULL DEFAULT 0",
        "output_truncated": "INTEGER NOT NULL DEFAULT 0",
        "exception_type": "TEXT",
    },
    "filter_decisions": {
        "severity": "TEXT NOT NULL DEFAULT 'info'",
    },
    "findings": {
        "finding_id": "TEXT NOT NULL DEFAULT ''",
        "schema_version": "INTEGER NOT NULL DEFAULT 1",
        "rule_id": "TEXT NOT NULL DEFAULT ''",
        "hunk_header": "TEXT NOT NULL DEFAULT ''",
        "context_before_json": "TEXT NOT NULL DEFAULT '[]'",
        "context_after_json": "TEXT NOT NULL DEFAULT '[]'",
    },
}


class ReviewStore(ABC):
    """Storage interface used by the pipeline."""

    @staticmethod
    def from_url(url: str | Path) -> "ReviewStore":
        """Create a store from a SQL-style URL.

        SQLite is the default implementation. Other SQL schemes are kept as
        explicit extension points so callers get a stable factory API without a
        partially implemented backend.
        """
        if isinstance(url, Path):
            return SQLiteReviewStore(url)
        raw = str(url)
        parsed = urlparse(raw)
        if not parsed.scheme:
            return SQLiteReviewStore(Path(raw))
        if parsed.scheme == "sqlite":
            if parsed.netloc and parsed.netloc not in {"", "localhost"}:
                raise ValueError("sqlite URL must be local, for example sqlite:///tmp/reviews.sqlite")
            path = unquote(parsed.path)
            if not path:
                raise ValueError("sqlite URL requires a database path")
            return SQLiteReviewStore(Path(path))
        if parsed.scheme in {"postgresql", "postgres", "mysql"}:
            raise NotImplementedError(
                f"{parsed.scheme} ReviewStore requires an optional SQLAlchemyReviewStore backend; "
                "the example keeps SQLite as the default implementation.")
        raise ValueError(f"Unsupported review store URL scheme: {parsed.scheme}")

    @abstractmethod
    def create_task(self, task_id: str, *, source: str, created_at: str, diff_summary: dict[str, Any],
                    diff_text: str) -> None:
        """Create a review task and persist its input diff summary."""

    @abstractmethod
    def save_filter_decisions(self, task_id: str, decisions: list[FilterDecision]) -> None:
        """Persist Filter allow/deny/manual-review decisions."""

    @abstractmethod
    def save_sandbox_runs(self, task_id: str, runs: list[SandboxRun]) -> None:
        """Persist sandbox execution summaries."""

    @abstractmethod
    def save_findings(self, task_id: str, bucket: str, findings: list[Finding]) -> None:
        """Persist findings, warnings, or human-review items."""

    @abstractmethod
    def save_monitoring(self, task_id: str, summary: MonitoringSummary) -> None:
        """Persist the monitoring summary for a review task."""

    @abstractmethod
    def complete_task(self, report: ReviewReport, markdown: str, *, completed_at: str) -> None:
        """Persist final report content and mark the task complete."""

    @abstractmethod
    def get_task_bundle(self, task_id: str) -> dict[str, Any]:
        """Return the complete persisted task bundle."""


class SQLiteReviewStore(ReviewStore):

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            self._initialize_schema(conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(SCHEMA)
        self._reconcile_schema(conn)
        applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
        if SCHEMA_VERSION not in applied:
            conn.executescript(INDEXES)
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, datetime('now'))",
                (SCHEMA_VERSION, ),
            )
        else:
            conn.executescript(INDEXES)

    def _reconcile_schema(self, conn: sqlite3.Connection) -> None:
        for table, columns in COLUMN_MIGRATIONS.items():
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for column, definition in columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def create_task(self, task_id: str, *, source: str, created_at: str, diff_summary: dict[str, Any],
                    diff_text: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO review_tasks(task_id, status, source, created_at) VALUES (?, ?, ?, ?)",
                (task_id, "running", source, created_at),
            )
            conn.execute(
                "INSERT INTO input_diffs(task_id, diff_summary_json, diff_text) VALUES (?, ?, ?)",
                (task_id, json.dumps(diff_summary, sort_keys=True), diff_text),
            )

    def save_filter_decisions(self, task_id: str, decisions: list[FilterDecision]) -> None:
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO filter_decisions(task_id, decision, reason, command, path, policy, severity)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [(task_id, d.decision, d.reason, d.command, d.path, d.policy, d.severity) for d in decisions],
            )

    def save_sandbox_runs(self, task_id: str, runs: list[SandboxRun]) -> None:
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO sandbox_runs(task_id, name, runtime, command, status, exit_code, duration_ms,
                                         stdout, stderr,
                                         timed_out, output_truncated, exception_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [(
                    task_id,
                    r.name,
                    r.runtime,
                    r.command,
                    r.status,
                    r.exit_code,
                    r.duration_ms,
                    r.stdout,
                    r.stderr,
                    int(r.timed_out),
                    int(r.output_truncated),
                    r.exception_type,
                ) for r in runs],
            )

    def save_findings(self, task_id: str, bucket: str, findings: list[Finding]) -> None:
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO findings(task_id, bucket, finding_id, schema_version, severity, category, file, line,
                                     title, evidence, recommendation, confidence, source, rule_id, hunk_header,
                                     context_before_json, context_after_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [(
                    task_id,
                    bucket,
                    f.finding_id,
                    f.schema_version,
                    f.severity,
                    f.category,
                    f.file,
                    f.line,
                    f.title,
                    f.evidence,
                    f.recommendation,
                    f.confidence,
                    f.source,
                    f.rule_id,
                    f.hunk_header,
                    json.dumps(f.context_before),
                    json.dumps(f.context_after),
                ) for f in findings],
            )

    def save_monitoring(self, task_id: str, summary: MonitoringSummary) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO monitoring_summaries(task_id, summary_json) VALUES (?, ?)",
                (task_id, json.dumps(summary.to_dict(), sort_keys=True)),
            )

    def complete_task(self, report: ReviewReport, markdown: str, *, completed_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE review_tasks SET status = ?, completed_at = ?, conclusion = ? WHERE task_id = ?",
                (report.status, completed_at, report.conclusion, report.task_id),
            )
            conn.execute(
                "INSERT OR REPLACE INTO review_reports(task_id, report_json, report_markdown) VALUES (?, ?, ?)",
                (report.task_id, json.dumps(report.to_dict(), sort_keys=True), markdown),
            )

    def get_task_bundle(self, task_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            task = _row_to_dict(conn.execute("SELECT * FROM review_tasks WHERE task_id = ?", (task_id, )).fetchone())
            diff = _row_to_dict(conn.execute("SELECT * FROM input_diffs WHERE task_id = ?", (task_id, )).fetchone())
            runs = [_row_to_dict(r) for r in conn.execute("SELECT * FROM sandbox_runs WHERE task_id = ?", (task_id, ))]
            filters = [
                _row_to_dict(r) for r in conn.execute("SELECT * FROM filter_decisions WHERE task_id = ?", (task_id, ))
            ]
            findings = [_row_to_dict(r) for r in conn.execute("SELECT * FROM findings WHERE task_id = ?", (task_id, ))]
            monitoring = _row_to_dict(
                conn.execute("SELECT * FROM monitoring_summaries WHERE task_id = ?", (task_id, )).fetchone())
            report = _row_to_dict(
                conn.execute("SELECT * FROM review_reports WHERE task_id = ?", (task_id, )).fetchone())
        return {
            "task": task,
            "input_diff": diff,
            "sandbox_runs": runs,
            "filter_decisions": filters,
            "findings": findings,
            "monitoring": monitoring,
            "report": report,
        }


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}
