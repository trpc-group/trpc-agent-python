"""SQLite implementation of the review store."""

import json
import os
import sqlite3
import stat
from datetime import datetime
from pathlib import Path

from reports.models import ReviewReport
from reports.models import ReviewScope
from security import redact_report
from security import redact_text

from .base import BaseReviewStore
from .records import filter_decision_rows
from .records import finding_rows
from .records import sandbox_rows
from .schema_loader import read_trusted_schema

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class SQLiteReviewStore(BaseReviewStore):
    """Persist normalized audit rows and the complete validated report."""

    def __init__(
        self,
        database_path: Path,
        schema_path: Path = SCHEMA_PATH,
    ) -> None:
        self.database_path = database_path
        self.schema_path = schema_path

    def initialize(self) -> None:
        """Create the normalized review schema."""
        schema_sql = self._read_trusted_schema()
        self.database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._secure_database_file()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.set_authorizer(self._schema_authorizer)
            try:
                connection.executescript(schema_sql)
            finally:
                connection.set_authorizer(None)
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(review_inputs)")
            }
            if "review_profile" not in columns:
                connection.execute(
                    "ALTER TABLE review_inputs ADD COLUMN review_profile TEXT "
                    "NOT NULL DEFAULT 'legacy'"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_review_inputs_digest_profile "
                "ON review_inputs(digest, review_profile)"
            )
        self.database_path.chmod(0o600)

    def _read_trusted_schema(self) -> str:
        """Read a bounded schema file confined to this example's storage directory."""
        return read_trusted_schema(self.schema_path, SCHEMA_PATH.parent, "SQLite")

    @staticmethod
    def _schema_authorizer(
        action: int,
        argument_one: str | None,
        argument_two: str | None,
        database_name: str | None,
        trigger_name: str | None,
    ) -> int:
        """Prevent configurable schema SQL from escaping or adding executable hooks."""
        del argument_two, database_name, trigger_name
        denied = {
            sqlite3.SQLITE_ATTACH,
            sqlite3.SQLITE_DETACH,
            sqlite3.SQLITE_CREATE_TRIGGER,
            sqlite3.SQLITE_CREATE_VIEW,
            sqlite3.SQLITE_CREATE_VTABLE,
            sqlite3.SQLITE_DROP_INDEX,
            sqlite3.SQLITE_DROP_TABLE,
            sqlite3.SQLITE_DROP_TRIGGER,
            sqlite3.SQLITE_DROP_VIEW,
        }
        if action in denied:
            return sqlite3.SQLITE_DENY
        if action in {sqlite3.SQLITE_DELETE, sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE}:
            if argument_one not in {"sqlite_master", "sqlite_schema"}:
                return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA and argument_one != "foreign_keys":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def _secure_database_file(self) -> None:
        """Create the database with private permissions before SQLite opens it."""
        try:
            metadata = os.lstat(self.database_path)
        except FileNotFoundError:
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.database_path, flags, 0o600)
            os.close(descriptor)
            return
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ValueError("SQLite path must be a regular file, not a link")
        if metadata.st_size:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.database_path, flags)
            try:
                header = os.read(descriptor, 16)
            finally:
                os.close(descriptor)
            if header != b"SQLite format 3\x00":
                raise ValueError("Refusing to overwrite a non-SQLite database file")
        self.database_path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def start_task(
        self,
        task_id: str,
        created_at: datetime,
        repository: str,
        scope: ReviewScope,
    ) -> None:
        """Insert the audit row before any untrusted review execution starts."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO review_tasks
                    (task_id, created_at, completed_at, status, repository, scope, conclusion)
                VALUES (?, ?, ?, 'running', ?, ?, '')
                ON CONFLICT(task_id) DO UPDATE SET
                    status = 'running', repository = excluded.repository,
                    scope = excluded.scope, conclusion = ''
                """,
                (
                    task_id,
                    created_at.isoformat(),
                    created_at.isoformat(),
                    redact_text(repository),
                    scope.value,
                ),
            )

    def mark_task_failed(
        self,
        task_id: str,
        completed_at: datetime,
        conclusion: str,
    ) -> None:
        """Leave a terminal audit status when report generation cannot finish."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE review_tasks
                SET status = 'failed', completed_at = ?, conclusion = ?
                WHERE task_id = ?
                """,
                (completed_at.isoformat(), redact_text(conclusion), task_id),
            )

    def save(self, report: ReviewReport) -> None:
        """Atomically replace all persisted data for one task."""
        report = redact_report(report)
        # The connection context commits every normalized row as one transaction.
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO review_tasks
                    (task_id, created_at, completed_at, status, repository, scope, conclusion)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    created_at = excluded.created_at,
                    completed_at = excluded.completed_at,
                    status = excluded.status,
                    repository = excluded.repository,
                    scope = excluded.scope,
                    conclusion = excluded.conclusion
                """,
                (
                    report.task_id,
                    report.created_at.isoformat(),
                    report.completed_at.isoformat(),
                    report.status,
                    report.repository,
                    report.scope.value,
                    redact_text(report.conclusion),
                ),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO review_inputs
                    (task_id, kind, source, digest, review_profile, file_count, hunk_count,
                     added_lines, removed_lines, files_json, redacted_preview)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.task_id,
                    report.input_summary.kind,
                    report.input_summary.source,
                    report.input_summary.digest,
                    report.input_summary.review_profile,
                    report.input_summary.file_count,
                    report.input_summary.hunk_count,
                    report.input_summary.added_lines,
                    report.input_summary.removed_lines,
                    json.dumps(report.input_summary.files, ensure_ascii=False),
                    redact_text(report.input_summary.redacted_preview),
                ),
            )
            # Re-saving a task replaces child rows while preserving referential integrity.
            for table in (
                "sandbox_runs",
                "filter_decisions",
                "findings",
                "monitoring_summaries",
                "review_reports",
            ):
                connection.execute(f"DELETE FROM {table} WHERE task_id = ?", (report.task_id,))

            connection.executemany(
                """
                INSERT INTO sandbox_runs
                    (run_id, task_id, command, status, duration_ms, exit_code,
                     timed_out, output_truncated, stdout_summary, stderr_summary, error_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                sandbox_rows(report),
            )
            connection.executemany(
                """
                INSERT INTO filter_decisions
                    (decision_id, task_id, command, decision, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                filter_decision_rows(report),
            )
            connection.executemany(
                """
                INSERT INTO findings
                    (finding_id, task_id, bucket, severity, category, file, line,
                     title, evidence, recommendation, confidence, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                finding_rows(report),
            )
            connection.execute(
                """
                INSERT INTO monitoring_summaries
                    (task_id, total_duration_ms, sandbox_duration_ms, tool_call_count,
                     blocked_count, finding_count, severity_distribution_json,
                     exception_distribution_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.task_id,
                    report.monitoring.total_duration_ms,
                    report.monitoring.sandbox_duration_ms,
                    report.monitoring.tool_call_count,
                    report.monitoring.blocked_count,
                    report.monitoring.finding_count,
                    json.dumps(report.monitoring.severity_distribution, sort_keys=True),
                    json.dumps(report.monitoring.exception_distribution, sort_keys=True),
                ),
            )
            connection.execute(
                """
                INSERT INTO review_reports (task_id, report_json)
                VALUES (?, ?)
                """,
                (
                    report.task_id,
                    report.model_dump_json(),
                ),
            )

    def get(self, task_id: str) -> ReviewReport | None:
        """Load and validate one report, if present."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT report_json FROM review_reports WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None

        return ReviewReport.model_validate_json(row[0])

    def get_latest_by_input_digest(
        self,
        digest: str,
        review_profile: str,
    ) -> ReviewReport | None:
        """Load the newest successful report for the exact input digest."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT reports.report_json
                FROM review_inputs AS inputs
                JOIN review_tasks AS tasks ON tasks.task_id = inputs.task_id
                JOIN review_reports AS reports ON reports.task_id = inputs.task_id
                WHERE inputs.digest = ? AND inputs.review_profile = ?
                    AND tasks.status NOT IN ('failed', 'running')
                ORDER BY tasks.completed_at DESC
                LIMIT 1
                """,
                (digest, review_profile),
            ).fetchone()
        if row is None:
            return None
        return ReviewReport.model_validate_json(row[0])

    def get_task_details(self, task_id: str) -> dict[str, object] | None:
        """Return normalized audit records for one task."""
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            task = connection.execute(
                "SELECT * FROM review_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if task is None:
                return None

            def rows(table: str) -> list[dict[str, object]]:
                result = connection.execute(
                    f"SELECT * FROM {table} WHERE task_id = ?",
                    (task_id,),
                ).fetchall()
                return [dict(item) for item in result]

            input_row = connection.execute(
                "SELECT * FROM review_inputs WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            monitoring = connection.execute(
                "SELECT * FROM monitoring_summaries WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            report = connection.execute(
                "SELECT report_json FROM review_reports WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            return {
                "task": dict(task),
                "input": dict(input_row) if input_row else None,
                "sandbox_runs": rows("sandbox_runs"),
                "filter_decisions": rows("filter_decisions"),
                "findings": rows("findings"),
                "monitoring": dict(monitoring) if monitoring else None,
                "report": json.loads(report["report_json"]) if report else None,
            }
