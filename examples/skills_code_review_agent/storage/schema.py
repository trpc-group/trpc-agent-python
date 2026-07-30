# Tencent is pleased to support the open source community by making trpc-agent-python available.
# Copyright (C) 2025 Tencent. All rights reserved.
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""SQLite storage schema and repository for review data."""

import sqlite3
import json
from datetime import datetime, timezone
from typing import Any, Optional


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS review_task (
    id TEXT PRIMARY KEY,
    input_type TEXT NOT NULL DEFAULT 'diff',
    diff_summary TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    total_duration_ms INTEGER DEFAULT 0,
    file_count INTEGER DEFAULT 0,
    total_added_lines INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS finding (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    file TEXT,
    line INTEGER,
    title TEXT NOT NULL,
    evidence TEXT,
    recommendation TEXT,
    confidence REAL DEFAULT 0.85,
    rule_id TEXT,
    is_warning INTEGER DEFAULT 0,
    FOREIGN KEY (task_id) REFERENCES review_task(id)
);

CREATE TABLE IF NOT EXISTS sandbox_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    script TEXT,
    exit_code INTEGER,
    stdout TEXT,
    stderr TEXT,
    duration_ms INTEGER DEFAULT 0,
    timed_out INTEGER DEFAULT 0,
    FOREIGN KEY (task_id) REFERENCES review_task(id)
);

CREATE TABLE IF NOT EXISTS filter_decision (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    rule TEXT,
    action TEXT NOT NULL,
    reason TEXT,
    FOREIGN KEY (task_id) REFERENCES review_task(id)
);

CREATE TABLE IF NOT EXISTS monitoring (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    total_duration_ms INTEGER DEFAULT 0,
    sandbox_duration_ms INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    intercept_count INTEGER DEFAULT 0,
    finding_count INTEGER DEFAULT 0,
    severity_distribution TEXT,
    exception_distribution TEXT,
    FOREIGN KEY (task_id) REFERENCES review_task(id)
);

CREATE TABLE IF NOT EXISTS report (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    format TEXT NOT NULL,
    content TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES review_task(id)
);
"""


class ReviewStore:
    """SQLite-backed storage for code review data."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()

    def create_task(self, task_id: str, input_type: str = "diff",
                    diff_summary: str = "") -> None:
        self.conn.execute(
            "INSERT INTO review_task (id, input_type, diff_summary, status, created_at) "
            "VALUES (?, ?, ?, 'running', ?)",
            (task_id, input_type, diff_summary, datetime.now(timezone.utc).isoformat())
        )
        self.conn.commit()

    def complete_task(self, task_id: str, duration_ms: int,
                      file_count: int, added_lines: int) -> None:
        self.conn.execute(
            "UPDATE review_task SET status='completed', total_duration_ms=?, "
            "file_count=?, total_added_lines=?, completed_at=? WHERE id=?",
            (duration_ms, file_count, added_lines,
             datetime.now(timezone.utc).isoformat(), task_id)
        )
        self.conn.commit()

    def save_finding(self, task_id: str, finding: dict[str, Any],
                      is_warning: bool = False) -> int:
        cursor = self.conn.execute(
            "INSERT INTO finding (task_id, severity, category, file, line, title, "
            "evidence, recommendation, confidence, rule_id, is_warning) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, finding.get('severity'), finding.get('category'),
             finding.get('file'), finding.get('line'), finding.get('title'),
             finding.get('evidence'), finding.get('recommendation'),
             finding.get('confidence'), finding.get('rule_id'),
             1 if is_warning else 0)
        )
        self.conn.commit()
        return cursor.lastrowid

    def save_findings(self, task_id: str, findings: list[dict[str, Any]]) -> int:
        for f in findings:
            self.save_finding(task_id, f)
        return len(findings)

    def save_sandbox_run(self, task_id: str, run_result: dict[str, Any]) -> int:
        cursor = self.conn.execute(
            "INSERT INTO sandbox_run (task_id, script, exit_code, stdout, stderr, "
            "duration_ms, timed_out) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task_id, run_result.get('script', ''),
             run_result.get('exit_code', -1),
             run_result.get('stdout', ''),
             run_result.get('stderr', ''),
             run_result.get('duration_ms', 0),
             1 if run_result.get('timed_out') else 0)
        )
        self.conn.commit()
        return cursor.lastrowid

    def save_filter_decision(self, task_id: str, action: str,
                             rule: str = "", reason: str = "") -> int:
        cursor = self.conn.execute(
            "INSERT INTO filter_decision (task_id, rule, action, reason) VALUES (?, ?, ?, ?)",
            (task_id, rule, action, reason)
        )
        self.conn.commit()
        return cursor.lastrowid

    def save_monitoring(self, task_id: str, data: dict[str, Any]) -> int:
        cursor = self.conn.execute(
            "INSERT INTO monitoring (task_id, total_duration_ms, sandbox_duration_ms, "
            "tool_call_count, intercept_count, finding_count, severity_distribution, "
            "exception_distribution) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id,
             data.get('total_duration_ms', 0),
             data.get('sandbox_duration_ms', 0),
             data.get('tool_call_count', 0),
             data.get('intercept_count', 0),
             data.get('finding_count', 0),
             json.dumps(data.get('severity_distribution', {})),
             json.dumps(data.get('exception_distribution', {})))
        )
        self.conn.commit()
        return cursor.lastrowid

    def save_report(self, task_id: str, format_type: str, content: str) -> int:
        cursor = self.conn.execute(
            "INSERT INTO report (task_id, format, content, created_at) VALUES (?, ?, ?, ?)",
            (task_id, format_type, content, datetime.now(timezone.utc).isoformat())
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_task(self, task_id: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM review_task WHERE id=?", (task_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_findings(self, task_id: str, only_warnings: bool = False) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM finding WHERE task_id=? AND is_warning=? ORDER BY severity, file, line",
            (task_id, 1 if only_warnings else 0)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_task_details(self, task_id: str) -> dict[str, Any]:
        """Get complete review details: task, findings, warnings, sandbox runs, filter decisions."""
        return {
            'task': self.get_task(task_id),
            'findings': self.get_findings(task_id, only_warnings=False),
            'warnings': self.get_findings(task_id, only_warnings=True),
            'sandbox_runs': [
                dict(r) for r in
                self.conn.execute("SELECT * FROM sandbox_run WHERE task_id=?", (task_id,)).fetchall()
            ],
            'filter_decisions': [
                dict(r) for r in
                self.conn.execute("SELECT * FROM filter_decision WHERE task_id=?", (task_id,)).fetchall()
            ],
            'monitoring': dict(r) if (
                r := self.conn.execute("SELECT * FROM monitoring WHERE task_id=?", (task_id,)).fetchone()
            ) else {},
        }
