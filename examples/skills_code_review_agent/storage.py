"""SQLite persistence with a backend-neutral review-store interface."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from models import ReviewReport


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS review_tasks (
  task_id TEXT PRIMARY KEY, status TEXT NOT NULL, input_sha256 TEXT NOT NULL,
  input_summary_json TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sandbox_runs (
  id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES review_tasks(task_id),
  command_json TEXT NOT NULL, status TEXT NOT NULL, exit_code INTEGER,
  duration_ms REAL NOT NULL, output_summary TEXT NOT NULL, error_type TEXT
);
CREATE TABLE IF NOT EXISTS filter_blocks (
  id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES review_tasks(task_id),
  decision TEXT NOT NULL, reason TEXT NOT NULL, command_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS findings (
  id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES review_tasks(task_id),
  severity TEXT NOT NULL, category TEXT NOT NULL, file TEXT NOT NULL, line INTEGER NOT NULL,
  title TEXT NOT NULL, evidence TEXT NOT NULL, recommendation TEXT NOT NULL,
  confidence REAL NOT NULL, source TEXT NOT NULL, rule_id TEXT NOT NULL, is_warning INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
  task_id TEXT PRIMARY KEY REFERENCES review_tasks(task_id), conclusion TEXT NOT NULL,
  report_json TEXT NOT NULL, monitoring_json TEXT NOT NULL
);
"""


class ReviewStore:
    def create_task(self, task_id: str, input_sha256: str, summary: dict[str, Any]) -> None:
        raise NotImplementedError

    def save_report(self, report: ReviewReport) -> None:
        raise NotImplementedError

    def get_task(self, task_id: str) -> dict[str, Any]:
        raise NotImplementedError


class SQLiteReviewStore(ReviewStore):
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def create_task(self, task_id: str, input_sha256: str, summary: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO review_tasks(task_id,status,input_sha256,input_summary_json) VALUES(?,?,?,?)",
                (task_id, "running", input_sha256, json.dumps(summary, ensure_ascii=False)),
            )

    def save_report(self, report: ReviewReport) -> None:
        payload = report.to_dict()
        with self._connect() as connection:
            for run in report.sandbox_runs:
                connection.execute(
                    "INSERT INTO sandbox_runs(task_id,command_json,status,exit_code,duration_ms,"
                    "output_summary,error_type) VALUES(?,?,?,?,?,?,?)",
                    (report.task_id, json.dumps(run.command), run.status, run.exit_code,
                     run.duration_ms, run.output[:1000], run.error_type),
                )
                if run.status == "blocked":
                    connection.execute(
                        "INSERT INTO filter_blocks(task_id,decision,reason,command_json) VALUES(?,?,?,?)",
                        (report.task_id, run.filter_decision, run.filter_reason or "", json.dumps(run.command)),
                    )
            for finding, warning in [*( (item, 0) for item in report.findings),
                                     *( (item, 1) for item in report.warnings)]:
                connection.execute(
                    "INSERT INTO findings(task_id,severity,category,file,line,title,evidence,recommendation,"
                    "confidence,source,rule_id,is_warning) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (report.task_id, finding.severity, finding.category, finding.file, finding.line,
                     finding.title, finding.evidence, finding.recommendation, finding.confidence,
                     finding.source, finding.rule_id, warning),
                )
            connection.execute(
                "INSERT INTO reports(task_id,conclusion,report_json,monitoring_json) VALUES(?,?,?,?)",
                (report.task_id, report.conclusion, json.dumps(payload, ensure_ascii=False),
                 json.dumps(report.monitoring, ensure_ascii=False)),
            )
            connection.execute(
                "UPDATE review_tasks SET status=? WHERE task_id=?", (report.status, report.task_id)
            )

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            task = connection.execute(
                "SELECT * FROM review_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if task is None:
                raise KeyError(task_id)
            output = dict(task)
            for table in ("sandbox_runs", "filter_blocks", "findings"):
                output[table] = [
                    dict(row) for row in connection.execute(
                        f"SELECT * FROM {table} WHERE task_id=? ORDER BY id", (task_id,)
                    )
                ]
            report = connection.execute(
                "SELECT * FROM reports WHERE task_id=?", (task_id,)
            ).fetchone()
            output["report"] = dict(report) if report else None
            return output
