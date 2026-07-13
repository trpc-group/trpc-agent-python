"""PostgreSQL implementation of the review store."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import urlsplit

from reports.models import ReviewReport
from reports.models import ReviewScope
from security import redact_report
from security import redact_text

from .base import BaseReviewStore
from .records import filter_decision_rows
from .records import finding_rows
from .records import sandbox_rows
from .schema_loader import read_trusted_schema

SCHEMA_PATH = Path(__file__).with_name("postgres_schema.sql")
MAX_DSN_BYTES = 8192
REMOTE_TLS_MODES = {"require", "verify-ca", "verify-full"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_SCHEMA_PREFIXES = (
    "CREATE TABLE IF NOT EXISTS public.",
    "ALTER TABLE public.review_inputs ADD COLUMN IF NOT EXISTS ",
    "CREATE INDEX IF NOT EXISTS ",
    "CREATE UNIQUE INDEX IF NOT EXISTS ",
)


class PostgreSQLStorageError(RuntimeError):
    """Sanitized storage failure safe to surface through the CLI."""


def validate_postgres_dsn(dsn: str) -> str:
    """Validate a URL DSN without returning it in an exception message."""
    value = dsn.strip()
    if not value:
        raise ValueError("CODE_REVIEW_POSTGRES_DSN is required for PostgreSQL storage")
    if len(value.encode("utf-8")) > MAX_DSN_BYTES:
        raise ValueError(f"PostgreSQL DSN exceeds {MAX_DSN_BYTES} bytes")
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise ValueError("PostgreSQL DSN contains a forbidden control character")
    parsed = urlsplit(value)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("PostgreSQL DSN must use a postgres:// or postgresql:// URL")
    if parsed.fragment:
        raise ValueError("PostgreSQL DSN must not contain a URL fragment")
    try:
        host = parsed.hostname
        parsed.port
    except ValueError as error:
        raise ValueError("PostgreSQL DSN contains an invalid host or port") from error
    if host and host.lower() not in LOCAL_HOSTS:
        sslmode = parse_qs(parsed.query).get("sslmode", [""])[-1].lower()
        if sslmode not in REMOTE_TLS_MODES:
            raise ValueError(
                "Remote PostgreSQL storage requires sslmode=require, verify-ca, "
                "or verify-full"
            )
    return value


class PostgreSQLReviewStore(BaseReviewStore):
    """Persist normalized audit rows in PostgreSQL using short transactions."""

    def __init__(
        self,
        dsn: str,
        schema_path: Path = SCHEMA_PATH,
        *,
        connect_timeout_seconds: int = 5,
        statement_timeout_seconds: int = 15,
    ) -> None:
        self._dsn = validate_postgres_dsn(dsn)
        self.schema_path = schema_path
        self.connect_timeout_seconds = max(1, min(connect_timeout_seconds, 30))
        self.statement_timeout_seconds = max(1, min(statement_timeout_seconds, 60))

    @staticmethod
    def _load_driver() -> tuple[Any, Any]:
        """Import the optional driver only when PostgreSQL is selected."""
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as error:
            raise RuntimeError(
                "PostgreSQL storage requires the 'postgresql' optional dependency; "
                "install this example with --extra postgresql"
            ) from error
        return psycopg, dict_row

    def _connect(self) -> Any:
        """Open a bounded connection without exposing credentials in errors."""
        psycopg, _ = self._load_driver()
        timeout_ms = self.statement_timeout_seconds * 1000
        connection = None
        try:
            connection = psycopg.connect(
                self._dsn,
                connect_timeout=self.connect_timeout_seconds,
                application_name="skills-code-review-agent",
                options=f"-c statement_timeout={timeout_ms} -c lock_timeout={timeout_ms}",
            )
            connection.execute("SET search_path TO pg_catalog, public")
            return connection
        except Exception as error:
            try:
                if connection is not None:
                    connection.close()
            except Exception:
                pass
            message = redact_text(str(error))[:1000]
            raise PostgreSQLStorageError(
                f"PostgreSQL connection failed ({type(error).__name__}): {message}"
            ) from error

    @contextmanager
    def _operation(self, name: str) -> Iterator[Any]:
        """Run one transaction and sanitize every driver-side failure."""
        try:
            with self._connect() as connection:
                yield connection
        except PostgreSQLStorageError:
            raise
        except Exception as error:
            message = redact_text(str(error))[:1000]
            raise PostgreSQLStorageError(
                f"PostgreSQL {name} failed ({type(error).__name__}): {message}"
            ) from error

    def _schema_statements(self) -> list[str]:
        schema = read_trusted_schema(
            self.schema_path,
            SCHEMA_PATH.parent,
            "PostgreSQL",
        )
        statements = [item.strip() for item in schema.split(";") if item.strip()]
        for statement in statements:
            normalized = " ".join(statement.split())
            if not normalized.startswith(_SCHEMA_PREFIXES):
                raise ValueError("PostgreSQL schema contains a disallowed statement")
        return statements

    def initialize(self) -> None:
        """Create or migrate the normalized review schema."""
        statements = self._schema_statements()
        with self._operation("schema initialization") as connection:
            for statement in statements:
                connection.execute(statement)

    def start_task(
        self,
        task_id: str,
        created_at: datetime,
        repository: str,
        scope: ReviewScope,
    ) -> None:
        """Insert the running audit row before untrusted review execution."""
        with self._operation("task start") as connection:
            connection.execute(
                """
                INSERT INTO public.review_tasks
                    (task_id, created_at, completed_at, status, repository, scope, conclusion)
                VALUES (%s, %s, %s, 'running', %s, %s, '')
                ON CONFLICT(task_id) DO UPDATE SET
                    status = 'running', repository = EXCLUDED.repository,
                    scope = EXCLUDED.scope, conclusion = ''
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
        """Leave a terminal audit status when report generation aborts."""
        with self._operation("task failure audit") as connection:
            connection.execute(
                """
                UPDATE public.review_tasks
                SET status = 'failed', completed_at = %s, conclusion = %s
                WHERE task_id = %s
                """,
                (completed_at.isoformat(), redact_text(conclusion), task_id),
            )

    def save(self, report: ReviewReport) -> None:
        """Atomically replace all persisted rows for one redacted report."""
        report = redact_report(report)
        with self._operation("report save") as connection:
            connection.execute(
                """
                INSERT INTO public.review_tasks
                    (task_id, created_at, completed_at, status, repository, scope, conclusion)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(task_id) DO UPDATE SET
                    created_at = EXCLUDED.created_at,
                    completed_at = EXCLUDED.completed_at,
                    status = EXCLUDED.status,
                    repository = EXCLUDED.repository,
                    scope = EXCLUDED.scope,
                    conclusion = EXCLUDED.conclusion
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
                INSERT INTO public.review_inputs
                    (task_id, kind, source, digest, review_profile, file_count, hunk_count,
                     added_lines, removed_lines, files_json, redacted_preview)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                        CAST(%s AS JSONB), %s)
                ON CONFLICT(task_id) DO UPDATE SET
                    kind = EXCLUDED.kind,
                    source = EXCLUDED.source,
                    digest = EXCLUDED.digest,
                    review_profile = EXCLUDED.review_profile,
                    file_count = EXCLUDED.file_count,
                    hunk_count = EXCLUDED.hunk_count,
                    added_lines = EXCLUDED.added_lines,
                    removed_lines = EXCLUDED.removed_lines,
                    files_json = EXCLUDED.files_json,
                    redacted_preview = EXCLUDED.redacted_preview
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
            for table in (
                "sandbox_runs",
                "filter_decisions",
                "findings",
                "monitoring_summaries",
                "review_reports",
            ):
                connection.execute(
                    f"DELETE FROM public.{table} WHERE task_id = %s",
                    (report.task_id,),
                )

            sandbox_values = sandbox_rows(report)
            if sandbox_values:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO public.sandbox_runs
                            (run_id, task_id, command, status, duration_ms, exit_code,
                             timed_out, output_truncated, stdout_summary, stderr_summary,
                             error_type)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        sandbox_values,
                    )
            decision_values = filter_decision_rows(report)
            if decision_values:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO public.filter_decisions
                            (decision_id, task_id, command, decision, reason, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        decision_values,
                    )
            finding_values = finding_rows(report)
            if finding_values:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO public.findings
                            (finding_id, task_id, bucket, severity, category, file, line,
                             title, evidence, recommendation, confidence, source)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        finding_values,
                    )
            connection.execute(
                """
                INSERT INTO public.monitoring_summaries
                    (task_id, total_duration_ms, sandbox_duration_ms, tool_call_count,
                     blocked_count, finding_count, severity_distribution_json,
                     exception_distribution_json)
                VALUES (%s, %s, %s, %s, %s, %s, CAST(%s AS JSONB), CAST(%s AS JSONB))
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
                INSERT INTO public.review_reports (task_id, report_json)
                VALUES (%s, CAST(%s AS JSONB))
                """,
                (report.task_id, report.model_dump_json()),
            )

    @staticmethod
    def _validate_report(value: object) -> ReviewReport:
        if isinstance(value, str):
            return ReviewReport.model_validate_json(value)
        return ReviewReport.model_validate(value)

    def get(self, task_id: str) -> ReviewReport | None:
        """Load and validate one report, if present."""
        with self._operation("report lookup") as connection:
            row = connection.execute(
                "SELECT report_json FROM public.review_reports WHERE task_id = %s",
                (task_id,),
            ).fetchone()
        return None if row is None else self._validate_report(row[0])

    def get_latest_by_input_digest(
        self,
        digest: str,
        review_profile: str,
    ) -> ReviewReport | None:
        """Load the newest successful report for the exact immutable input."""
        with self._operation("cache lookup") as connection:
            row = connection.execute(
                """
                SELECT reports.report_json
                FROM public.review_inputs AS inputs
                JOIN public.review_tasks AS tasks ON tasks.task_id = inputs.task_id
                JOIN public.review_reports AS reports ON reports.task_id = inputs.task_id
                WHERE inputs.digest = %s AND inputs.review_profile = %s
                    AND tasks.status NOT IN ('failed', 'running')
                ORDER BY tasks.completed_at DESC
                LIMIT 1
                """,
                (digest, review_profile),
            ).fetchone()
        return None if row is None else self._validate_report(row[0])

    def get_task_details(self, task_id: str) -> dict[str, object] | None:
        """Return normalized audit records for one task."""
        _, dict_row = self._load_driver()
        with self._operation("task detail lookup") as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    "SELECT * FROM public.review_tasks WHERE task_id = %s",
                    (task_id,),
                )
                task = cursor.fetchone()
                if task is None:
                    return None

                def rows(table: str) -> list[dict[str, object]]:
                    cursor.execute(
                        f"SELECT * FROM public.{table} WHERE task_id = %s",
                        (task_id,),
                    )
                    return list(cursor.fetchall())

                cursor.execute(
                    "SELECT * FROM public.review_inputs WHERE task_id = %s",
                    (task_id,),
                )
                input_row = cursor.fetchone()
                cursor.execute(
                    "SELECT * FROM public.monitoring_summaries WHERE task_id = %s",
                    (task_id,),
                )
                monitoring = cursor.fetchone()
                cursor.execute(
                    "SELECT report_json FROM public.review_reports WHERE task_id = %s",
                    (task_id,),
                )
                report = cursor.fetchone()
                return {
                    "task": dict(task),
                    "input": dict(input_row) if input_row else None,
                    "sandbox_runs": rows("sandbox_runs"),
                    "filter_decisions": rows("filter_decisions"),
                    "findings": rows("findings"),
                    "monitoring": dict(monitoring) if monitoring else None,
                    "report": report["report_json"] if report else None,
                }
