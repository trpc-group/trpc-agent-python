"""Pluggable persistence for code review tasks.

SQLite is the bundled implementation.  Callers should depend on
``ReviewStoreBase`` or use :func:`create_store` when the backend is selected
from configuration.  ``ReviewStore`` remains an alias for the SQLite class so
the original example API keeps working.
"""

from __future__ import annotations

import json
import re
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .models import FilterDecision, Finding, ReviewMetrics, SandboxRun, utc_now_iso

SCHEMA_VERSION = 1
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema.sql"

_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
_TASK_CHILD_TABLES = (
    "sandbox_run",
    "finding",
    "filter_intercept",
    "review_metric",
    "review_report",
)


class TaskExistsError(RuntimeError):
    """Raised when a caller attempts to create an existing review task."""

    def __init__(self, task_id: str) -> None:
        super().__init__(f"review task already exists: {task_id}")
        self.task_id = task_id


class ReviewStoreBase(ABC):
    """Backend-neutral contract used by the review pipeline and utilities."""

    @abstractmethod
    def close(self) -> None:
        """Release backend resources."""

    @abstractmethod
    def init_schema(self) -> None:
        """Create or migrate the backend schema."""

    @abstractmethod
    def create_task(
        self,
        *,
        task_id: str,
        input_type: str,
        input_ref: str,
        diff_sha256: str,
        diff_summary: dict[str, Any],
        overwrite: bool = False,
    ) -> None:
        """Create a running task, optionally resetting an existing bundle."""

    @abstractmethod
    def update_task(self, task_id: str, *, status: str, final_conclusion: str) -> None:
        """Update task completion state."""

    @abstractmethod
    def add_sandbox_run(self, task_id: str, run: SandboxRun) -> None:
        """Persist a sandbox run."""

    @abstractmethod
    def add_filter_intercept(self, task_id: str, decision: FilterDecision, *, commit: bool = True) -> None:
        """Persist a filter decision that stopped execution."""

    @abstractmethod
    def add_finding(self, task_id: str, finding: Finding) -> None:
        """Persist a finding."""

    @abstractmethod
    def add_metrics(self, task_id: str, metrics: ReviewMetrics) -> None:
        """Persist review metrics."""

    @abstractmethod
    def add_report(self, task_id: str, report_json: dict[str, Any], report_md: str) -> None:
        """Persist the final report."""

    @abstractmethod
    def get_task(self, task_id: str) -> dict[str, Any]:
        """Return a complete task bundle."""

    @abstractmethod
    def list_tasks(self, *, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        """Return recent task summaries."""


class SqliteReviewStore(ReviewStoreBase):
    """SQLite implementation of :class:`ReviewStoreBase`."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.init_schema()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        """Initialize SQLite from ``schema.sql``, the sole DDL source."""
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        self.conn.executescript(schema_sql)
        self.conn.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, utc_now_iso()),
        )
        self.conn.commit()

    def create_task(
        self,
        *,
        task_id: str,
        input_type: str,
        input_ref: str,
        diff_sha256: str,
        diff_summary: dict[str, Any],
        overwrite: bool = False,
    ) -> None:
        """Create a task without silently destroying a previous review.

        Duplicate ids raise :class:`TaskExistsError` by default.  Explicit
        ``overwrite=True`` atomically resets the parent row and removes all
        child rows, so a new run cannot be mixed with an old task bundle.  If
        any statement fails, SQLite rolls the complete reset back.
        """
        encoded_summary = json.dumps(diff_summary, ensure_ascii=False)
        started_at = utc_now_iso()

        try:
            with self.conn:
                exists = self.conn.execute(
                    "SELECT 1 FROM review_task WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                if exists and not overwrite:
                    raise TaskExistsError(task_id)

                if exists:
                    self.conn.execute(
                        """
                        UPDATE review_task
                           SET status = ?, input_type = ?, input_ref = ?, diff_sha256 = ?,
                               diff_summary = ?, started_at = ?, finished_at = NULL,
                               final_conclusion = ''
                         WHERE task_id = ?
                        """,
                        (
                            "running",
                            input_type,
                            input_ref,
                            diff_sha256,
                            encoded_summary,
                            started_at,
                            task_id,
                        ),
                    )
                    for table in _TASK_CHILD_TABLES:
                        self.conn.execute(f"DELETE FROM {table} WHERE task_id = ?", (task_id,))
                    return

                self.conn.execute(
                    """
                    INSERT INTO review_task(
                        task_id, status, input_type, input_ref, diff_sha256, diff_summary, started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (task_id, "running", input_type, input_ref, diff_sha256, encoded_summary, started_at),
                )
        except sqlite3.IntegrityError as exc:
            # A second connection may win the insert after our existence check.
            if not overwrite and self._one("SELECT 1 FROM review_task WHERE task_id = ?", (task_id,)):
                raise TaskExistsError(task_id) from exc
            raise

    def update_task(self, task_id: str, *, status: str, final_conclusion: str) -> None:
        self.conn.execute(
            """
            UPDATE review_task
               SET status = ?, finished_at = ?, final_conclusion = ?
             WHERE task_id = ?
            """,
            (status, utc_now_iso(), final_conclusion, task_id),
        )
        self.conn.commit()

    def add_sandbox_run(self, task_id: str, run: SandboxRun) -> None:
        self.conn.execute(
            """
            INSERT INTO sandbox_run(
                task_id, name, runtime, command, status, exit_code, timed_out, duration_ms,
                stdout, stderr, output_truncated, artifacts_json, error_type, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                run.name,
                run.runtime,
                run.command,
                run.status,
                run.exit_code,
                1 if run.timed_out else 0,
                run.duration_ms,
                run.stdout,
                run.stderr,
                1 if run.output_truncated else 0,
                json.dumps(run.artifacts, ensure_ascii=False),
                run.error_type,
                run.started_at,
                run.finished_at,
            ),
        )
        if run.filter_decision and run.filter_decision.action != "allow":
            self.add_filter_intercept(task_id, run.filter_decision, commit=False)
        self.conn.commit()

    def add_filter_intercept(self, task_id: str, decision: FilterDecision, *, commit: bool = True) -> None:
        self.conn.execute(
            """
            INSERT INTO filter_intercept(task_id, action, rule_id, reason, command, path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                decision.action,
                decision.rule_id,
                decision.reason,
                decision.command,
                decision.path,
                decision.created_at,
            ),
        )
        if commit:
            self.conn.commit()

    def add_finding(self, task_id: str, finding: Finding) -> None:
        self.conn.execute(
            """
            INSERT INTO finding(
                task_id, severity, category, file, line, title, evidence,
                recommendation, confidence, source, disposition
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                finding.severity,
                finding.category,
                finding.file,
                finding.line,
                finding.title,
                finding.evidence,
                finding.recommendation,
                finding.confidence,
                finding.source,
                finding.disposition,
            ),
        )
        self.conn.commit()

    def add_metrics(self, task_id: str, metrics: ReviewMetrics) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO review_metric(task_id, metrics_json) VALUES (?, ?)",
            (task_id, json.dumps(metrics.to_dict(), ensure_ascii=False)),
        )
        self.conn.commit()

    def add_report(self, task_id: str, report_json: dict[str, Any], report_md: str) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO review_report(task_id, report_json, report_md, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (task_id, json.dumps(report_json, ensure_ascii=False, indent=2), report_md, utc_now_iso()),
        )
        self.conn.commit()

    def get_task(self, task_id: str) -> dict[str, Any]:
        """Return a full task bundle by task id."""
        task = self._one("SELECT * FROM review_task WHERE task_id = ?", (task_id,))
        if not task:
            raise KeyError(f"task not found: {task_id}")
        sandbox_runs = self._many("SELECT * FROM sandbox_run WHERE task_id = ? ORDER BY id", (task_id,))
        findings = self._many("SELECT * FROM finding WHERE task_id = ? ORDER BY id", (task_id,))
        intercepts = self._many("SELECT * FROM filter_intercept WHERE task_id = ? ORDER BY id", (task_id,))
        metrics = self._one("SELECT * FROM review_metric WHERE task_id = ?", (task_id,))
        report = self._one("SELECT * FROM review_report WHERE task_id = ?", (task_id,))
        return {
            "task": self._decode_task(task),
            "sandbox_runs": [self._decode_sandbox(row) for row in sandbox_runs],
            "findings": [dict(row) for row in findings],
            "filter_intercepts": [dict(row) for row in intercepts],
            "metrics": json.loads(metrics["metrics_json"]) if metrics else {},
            "report": json.loads(report["report_json"]) if report else {},
        }

    def list_tasks(self, *, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        """Return newest task rows, optionally filtered by status."""
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if status is None:
            rows = self._many(
                "SELECT * FROM review_task ORDER BY started_at DESC, task_id DESC LIMIT ?",
                (limit,),
            )
        else:
            rows = self._many(
                """
                SELECT * FROM review_task
                 WHERE status = ?
                 ORDER BY started_at DESC, task_id DESC
                 LIMIT ?
                """,
                (status, limit),
            )
        return [self._decode_task(row) for row in rows]

    def _one(self, query: str, args: tuple[Any, ...]) -> sqlite3.Row | None:
        return self.conn.execute(query, args).fetchone()

    def _many(self, query: str, args: tuple[Any, ...]) -> list[sqlite3.Row]:
        return list(self.conn.execute(query, args).fetchall())

    @staticmethod
    def _decode_task(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["diff_summary"] = json.loads(data["diff_summary"])
        return data

    @staticmethod
    def _decode_sandbox(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["timed_out"] = bool(data["timed_out"])
        data["output_truncated"] = bool(data["output_truncated"])
        data["artifacts"] = json.loads(data.pop("artifacts_json"))
        return data


def create_store(database: Path | str) -> ReviewStoreBase:
    """Create a review store from a SQLite path or DSN.

    Supported forms include ``Path('review.sqlite3')``, ``':memory:'`` and
    ``sqlite:///review.sqlite3``.  PostgreSQL is recognized so configuration
    errors are explicit, but no PostgreSQL implementation is bundled yet.
    """
    if isinstance(database, Path):
        return SqliteReviewStore(database)

    value = str(database).strip()
    if not value:
        raise ValueError("database path or DSN must not be empty")

    lowered = value.lower()
    if lowered.startswith(("postgresql://", "postgres://", "postgresql+")):
        raise NotImplementedError("PostgreSQL review storage is not implemented; use a SQLite path or DSN")

    if lowered.startswith("sqlite:///"):
        path_text = unquote(value[len("sqlite:///"):])
        if not path_text:
            raise ValueError("SQLite DSN must include a database path")
        return SqliteReviewStore(path_text)

    if "://" in value and not _WINDOWS_PATH_RE.match(value):
        scheme = value.split(":", 1)[0]
        raise ValueError(f"unsupported review store DSN scheme: {scheme}")

    return SqliteReviewStore(value)


# Backward compatibility for run_agent.py, review_engine.py and existing users.
ReviewStore = SqliteReviewStore


__all__ = [
    "ReviewStore",
    "ReviewStoreBase",
    "SqliteReviewStore",
    "TaskExistsError",
    "create_store",
]
