# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""SQLite persistence for the code-review example."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Protocol

from .models import FilterEvent
from .models import Finding
from .models import InputSummary
from .models import ReviewReport
from .models import ReviewTask
from .models import SandboxRun
from .models import utc_now_iso
from .sanitizer import redact_mapping


class ReviewStoreProtocol(Protocol):
    """Minimal persistence contract consumed by the review pipeline."""

    def __enter__(self) -> "ReviewStoreProtocol":
        ...

    def __exit__(self, exc_type, exc, tb) -> None:
        ...

    def save_review(
        self,
        *,
        task: ReviewTask,
        input_summary: InputSummary,
        findings: list[Finding],
        warnings: list[Finding],
        needs_human_review: list[Finding],
        filter_events: list[FilterEvent],
        sandbox_runs: list[SandboxRun],
        report: ReviewReport,
        report_json_path: str | Path,
        report_md_path: str | Path,
    ) -> None:
        ...


ReviewStoreFactory = Callable[[str | Path], ReviewStoreProtocol]


class ReviewStore:
    """Small sqlite3-backed store for review task artifacts."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> "ReviewStore":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def save_review(
        self,
        *,
        task: ReviewTask,
        input_summary: InputSummary,
        findings: list[Finding],
        warnings: list[Finding],
        needs_human_review: list[Finding],
        filter_events: list[FilterEvent],
        sandbox_runs: list[SandboxRun],
        report: ReviewReport,
        report_json_path: str | Path,
        report_md_path: str | Path,
    ) -> None:
        """Persist a complete review result."""
        conn = self._require_conn()
        with conn:
            self._upsert_task(task)
            self._upsert_input_summary(task.id, input_summary)
            self._insert_findings(task.id, "finding", findings)
            self._insert_findings(task.id, "warning", warnings)
            self._insert_findings(task.id, "needs_human_review", needs_human_review)
            self._insert_filter_events(filter_events)
            self._insert_sandbox_runs(sandbox_runs)
            self._upsert_metrics(task.id, report)
            self._upsert_report(task.id, report, report_json_path, report_md_path)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self._fetch_one("SELECT * FROM review_task WHERE id = ?", (task_id, ))

    def get_input_summary(self, task_id: str) -> dict[str, Any] | None:
        row = self._fetch_one("SELECT * FROM input_diff WHERE task_id = ?", (task_id, ))
        if row and row.get("summary_json"):
            row["summary_json"] = json.loads(row["summary_json"])
        return row

    def list_findings(self, task_id: str) -> list[dict[str, Any]]:
        return self._fetch_all("SELECT * FROM finding WHERE task_id = ? ORDER BY route, severity, category, file, line",
                               (task_id, ))

    def list_filter_events(self, task_id: str) -> list[dict[str, Any]]:
        rows = self._fetch_all("SELECT * FROM filter_event WHERE task_id = ? ORDER BY created_at, id", (task_id, ))
        for row in rows:
            row["metadata_json"] = json.loads(row["metadata_json"] or "{}")
        return rows

    def list_sandbox_runs(self, task_id: str) -> list[dict[str, Any]]:
        return self._fetch_all("SELECT * FROM sandbox_run WHERE task_id = ? ORDER BY created_at, id", (task_id, ))

    def get_report(self, task_id: str) -> dict[str, Any] | None:
        row = self._fetch_one("SELECT * FROM report WHERE task_id = ?", (task_id, ))
        if row and row.get("report_json"):
            row["report_json"] = json.loads(row["report_json"])
        return row

    def get_metrics(self, task_id: str) -> dict[str, Any] | None:
        row = self._fetch_one("SELECT * FROM review_metrics WHERE task_id = ?", (task_id, ))
        if row:
            row["severity_distribution_json"] = json.loads(row["severity_distribution_json"] or "{}")
            row["category_distribution_json"] = json.loads(row["category_distribution_json"] or "{}")
            row["exception_distribution_json"] = json.loads(row["exception_distribution_json"] or "{}")
        return row

    def _create_schema(self) -> None:
        conn = self._require_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS review_task (
              id TEXT PRIMARY KEY,
              input_type TEXT NOT NULL,
              input_ref TEXT NOT NULL,
              status TEXT NOT NULL,
              summary TEXT NOT NULL,
              created_at TEXT NOT NULL,
              finished_at TEXT
            );

            CREATE TABLE IF NOT EXISTS input_diff (
              task_id TEXT PRIMARY KEY,
              raw_diff_sha256 TEXT NOT NULL,
              file_count INTEGER NOT NULL,
              hunk_count INTEGER NOT NULL,
              added_lines INTEGER NOT NULL,
              deleted_lines INTEGER NOT NULL,
              summary_json TEXT NOT NULL,
              FOREIGN KEY(task_id) REFERENCES review_task(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS finding (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              task_id TEXT NOT NULL,
              fingerprint TEXT NOT NULL,
              route TEXT NOT NULL,
              severity TEXT NOT NULL,
              category TEXT NOT NULL,
              file TEXT NOT NULL,
              line INTEGER,
              title TEXT NOT NULL,
              evidence TEXT NOT NULL,
              recommendation TEXT NOT NULL,
              confidence REAL NOT NULL,
              source TEXT NOT NULL,
              UNIQUE(task_id, fingerprint, route),
              FOREIGN KEY(task_id) REFERENCES review_task(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS filter_event (
              id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              decision TEXT NOT NULL,
              reason_code TEXT NOT NULL,
              target_type TEXT NOT NULL,
              target TEXT NOT NULL,
              command TEXT NOT NULL,
              runtime TEXT,
              cwd TEXT NOT NULL,
              script_path TEXT NOT NULL,
              timeout_sec REAL,
              output_limit_bytes INTEGER,
              metadata_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(task_id) REFERENCES review_task(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS sandbox_run (
              id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              runtime TEXT NOT NULL,
              command TEXT NOT NULL,
              status TEXT NOT NULL,
              exit_code INTEGER,
              duration_ms INTEGER NOT NULL,
              stdout TEXT NOT NULL,
              stderr TEXT NOT NULL,
              timeout_sec REAL,
              output_limit_bytes INTEGER,
              stdout_truncated INTEGER NOT NULL,
              stderr_truncated INTEGER NOT NULL,
              error_type TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(task_id) REFERENCES review_task(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS review_metrics (
              task_id TEXT PRIMARY KEY,
              total_duration_ms INTEGER NOT NULL,
              sandbox_duration_ms INTEGER NOT NULL,
              tool_call_count INTEGER NOT NULL,
              interception_count INTEGER NOT NULL,
              finding_count INTEGER NOT NULL,
              warning_count INTEGER NOT NULL,
              needs_human_review_count INTEGER NOT NULL,
              severity_distribution_json TEXT NOT NULL,
              category_distribution_json TEXT NOT NULL,
              exception_distribution_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(task_id) REFERENCES review_task(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS report (
              task_id TEXT PRIMARY KEY,
              json_path TEXT NOT NULL,
              md_path TEXT NOT NULL,
              report_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(task_id) REFERENCES review_task(id) ON DELETE CASCADE
            );
            """)

    def _upsert_task(self, task: ReviewTask) -> None:
        payload = redact_mapping(task.to_dict())
        self._require_conn().execute(
            """
            INSERT INTO review_task (id, input_type, input_ref, status, summary, created_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              input_type=excluded.input_type,
              input_ref=excluded.input_ref,
              status=excluded.status,
              summary=excluded.summary,
              finished_at=excluded.finished_at
            """,
            (
                payload["id"],
                payload["input_type"],
                payload["input_ref"],
                payload["status"],
                payload["summary"],
                payload["created_at"],
                payload["finished_at"],
            ),
        )

    def _upsert_input_summary(self, task_id: str, input_summary: InputSummary) -> None:
        payload = redact_mapping(input_summary.to_dict())
        self._require_conn().execute(
            """
            INSERT INTO input_diff (
              task_id, raw_diff_sha256, file_count, hunk_count, added_lines, deleted_lines, summary_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
              raw_diff_sha256=excluded.raw_diff_sha256,
              file_count=excluded.file_count,
              hunk_count=excluded.hunk_count,
              added_lines=excluded.added_lines,
              deleted_lines=excluded.deleted_lines,
              summary_json=excluded.summary_json
            """,
            (
                task_id,
                payload["raw_diff_sha256"],
                payload["file_count"],
                payload["hunk_count"],
                payload["added_lines"],
                payload["deleted_lines"],
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )

    def _insert_findings(self, task_id: str, route: str, findings: list[Finding]) -> None:
        for finding in findings:
            payload = redact_mapping(finding.to_dict())
            self._require_conn().execute(
                """
                INSERT OR IGNORE INTO finding (
                  task_id, fingerprint, route, severity, category, file, line, title,
                  evidence, recommendation, confidence, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    payload["fingerprint"] or "",
                    route,
                    payload["severity"],
                    payload["category"],
                    payload["file"],
                    payload["line"],
                    payload["title"],
                    payload["evidence"],
                    payload["recommendation"],
                    payload["confidence"],
                    payload["source"],
                ),
            )

    def _insert_filter_events(self, events: list[FilterEvent]) -> None:
        for event in events:
            payload = redact_mapping(event.to_dict())
            self._require_conn().execute(
                """
                INSERT OR REPLACE INTO filter_event (
                  id, task_id, decision, reason_code, target_type, target, command, runtime,
                  cwd, script_path, timeout_sec, output_limit_bytes, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["id"],
                    payload["task_id"],
                    payload["decision"],
                    payload["reason_code"],
                    payload["target_type"],
                    payload["target"],
                    payload["command"],
                    payload["runtime"],
                    payload["cwd"],
                    payload["script_path"],
                    payload["timeout_sec"],
                    payload["output_limit_bytes"],
                    json.dumps(payload["metadata"], ensure_ascii=False, sort_keys=True),
                    payload["created_at"],
                ),
            )

    def _insert_sandbox_runs(self, runs: list[SandboxRun]) -> None:
        for run in runs:
            payload = redact_mapping(run.to_dict())
            self._require_conn().execute(
                """
                INSERT OR REPLACE INTO sandbox_run (
                  id, task_id, runtime, command, status, exit_code, duration_ms, stdout, stderr,
                  timeout_sec, output_limit_bytes, stdout_truncated, stderr_truncated, error_type, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["id"],
                    payload["task_id"],
                    payload["runtime"],
                    payload["command"],
                    payload["status"],
                    payload["exit_code"],
                    payload["duration_ms"],
                    payload["stdout"],
                    payload["stderr"],
                    payload["timeout_sec"],
                    payload["output_limit_bytes"],
                    int(bool(payload["stdout_truncated"])),
                    int(bool(payload["stderr_truncated"])),
                    payload["error_type"],
                    payload["created_at"],
                ),
            )

    def _upsert_metrics(self, task_id: str, report: ReviewReport) -> None:
        payload = redact_mapping(report.metrics.to_dict())
        self._require_conn().execute(
            """
            INSERT INTO review_metrics (
              task_id, total_duration_ms, sandbox_duration_ms, tool_call_count, interception_count,
              finding_count, warning_count, needs_human_review_count, severity_distribution_json,
              category_distribution_json, exception_distribution_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
              total_duration_ms=excluded.total_duration_ms,
              sandbox_duration_ms=excluded.sandbox_duration_ms,
              tool_call_count=excluded.tool_call_count,
              interception_count=excluded.interception_count,
              finding_count=excluded.finding_count,
              warning_count=excluded.warning_count,
              needs_human_review_count=excluded.needs_human_review_count,
              severity_distribution_json=excluded.severity_distribution_json,
              category_distribution_json=excluded.category_distribution_json,
              exception_distribution_json=excluded.exception_distribution_json,
              created_at=excluded.created_at
            """,
            (
                task_id,
                payload["total_duration_ms"],
                payload["sandbox_duration_ms"],
                payload["tool_call_count"],
                payload["interception_count"],
                payload["finding_count"],
                payload["warning_count"],
                payload["needs_human_review_count"],
                json.dumps(payload["severity_distribution"], ensure_ascii=False, sort_keys=True),
                json.dumps(payload["category_distribution"], ensure_ascii=False, sort_keys=True),
                json.dumps(payload["exception_distribution"], ensure_ascii=False, sort_keys=True),
                utc_now_iso(),
            ),
        )

    def _upsert_report(
        self,
        task_id: str,
        report: ReviewReport,
        report_json_path: str | Path,
        report_md_path: str | Path,
    ) -> None:
        payload = redact_mapping(report.to_dict())
        self._require_conn().execute(
            """
            INSERT INTO report (task_id, json_path, md_path, report_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
              json_path=excluded.json_path,
              md_path=excluded.md_path,
              report_json=excluded.report_json,
              created_at=excluded.created_at
            """,
            (
                task_id,
                str(report_json_path),
                str(report_md_path),
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                utc_now_iso(),
            ),
        )

    def _fetch_one(self, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        row = self._require_conn().execute(query, params).fetchone()
        return dict(row) if row is not None else None

    def _fetch_all(self, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        rows = self._require_conn().execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("ReviewStore is not open")
        return self._conn
