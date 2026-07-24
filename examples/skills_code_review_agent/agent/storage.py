"""SQLite persistence for code review tasks."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import FilterDecision
from .models import Finding
from .models import ReviewMetrics
from .models import SandboxRun
from .models import utc_now_iso


SCHEMA_VERSION = 1


class ReviewStore:
    """Small SQLite storage layer with room to swap the SQL backend later."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.init_schema()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        """Create the review schema if needed."""
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS review_task (
                task_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                input_type TEXT NOT NULL,
                input_ref TEXT NOT NULL,
                diff_sha256 TEXT NOT NULL,
                diff_summary TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                final_conclusion TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS sandbox_run (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                name TEXT NOT NULL,
                runtime TEXT NOT NULL,
                command TEXT NOT NULL,
                status TEXT NOT NULL,
                exit_code INTEGER,
                timed_out INTEGER NOT NULL,
                duration_ms INTEGER NOT NULL,
                stdout TEXT NOT NULL,
                stderr TEXT NOT NULL,
                output_truncated INTEGER NOT NULL,
                artifacts_json TEXT NOT NULL,
                error_type TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES review_task(task_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS finding (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
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
                FOREIGN KEY(task_id) REFERENCES review_task(task_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS filter_intercept (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                action TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                command TEXT NOT NULL,
                path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES review_task(task_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS review_metric (
                task_id TEXT PRIMARY KEY,
                metrics_json TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES review_task(task_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS review_report (
                task_id TEXT PRIMARY KEY,
                report_json TEXT NOT NULL,
                report_md TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES review_task(task_id) ON DELETE CASCADE
            );
            """
        )
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
    ) -> None:
        self.conn.execute("DELETE FROM review_task WHERE task_id = ?", (task_id,))
        self.conn.execute(
            """
            INSERT INTO review_task(
                task_id, status, input_type, input_ref, diff_sha256, diff_summary, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, "running", input_type, input_ref, diff_sha256, json.dumps(diff_summary, ensure_ascii=False),
             utc_now_iso()),
        )
        self.conn.commit()

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
