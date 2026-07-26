"""SQLite audit storage for the review example."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager


class ReviewStorage:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS review_tasks (task_id TEXT PRIMARY KEY, status TEXT, input_digest TEXT);
                CREATE TABLE IF NOT EXISTS sandbox_runs (task_id TEXT, runtime TEXT, status TEXT, exit_code INTEGER, output TEXT);
                CREATE TABLE IF NOT EXISTS filter_decisions (task_id TEXT, decision TEXT, reason TEXT);
                CREATE TABLE IF NOT EXISTS findings (task_id TEXT, fingerprint TEXT, severity TEXT, category TEXT, file TEXT, line INTEGER, evidence TEXT, recommendation TEXT, confidence REAL, UNIQUE(task_id, fingerprint));
                CREATE TABLE IF NOT EXISTS review_reports (task_id TEXT PRIMARY KEY, report_json TEXT);
                CREATE TABLE IF NOT EXISTS review_metrics (task_id TEXT PRIMARY KEY, finding_count INTEGER, sandbox_run_count INTEGER, blocked_count INTEGER);
                CREATE TABLE IF NOT EXISTS skill_loads (task_id TEXT, operation TEXT, documents_json TEXT, result_json TEXT);
                CREATE TABLE IF NOT EXISTS skill_runs (task_id TEXT, runtime TEXT, command_json TEXT, status TEXT, exit_code INTEGER, output TEXT, stderr TEXT, timed_out INTEGER, duration_seconds REAL, output_files_json TEXT);
                CREATE TABLE IF NOT EXISTS model_runs (task_id TEXT, model TEXT, duration_seconds REAL, result_json TEXT);
                CREATE TABLE IF NOT EXISTS review_state_events (task_id TEXT, from_state TEXT, to_state TEXT, reason TEXT, timestamp REAL);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(review_metrics)")}
            if "metrics_json" not in columns:
                db.execute("ALTER TABLE review_metrics ADD COLUMN metrics_json TEXT")

    @contextmanager
    def _connect(self):
        """Commit/rollback and close each short-lived SQLite connection."""
        db = sqlite3.connect(self.path)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def save_native(self, task_id: str, report: dict, input_digest: str) -> None:
        """Persist the FunctionTool-owned report without depending on legacy models."""
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO review_tasks VALUES (?, ?, ?)", (task_id, report["status"], input_digest))
            db.execute("INSERT OR REPLACE INTO review_reports VALUES (?, ?)", (task_id, json.dumps(report, ensure_ascii=False)))
            metrics = report["metrics"]
            db.execute(
                "INSERT OR REPLACE INTO review_metrics (task_id, finding_count, sandbox_run_count, blocked_count, metrics_json) VALUES (?, ?, ?, ?, ?)",
                (task_id, metrics["finding_count"], metrics["sandbox_run_count"], metrics["blocked_count"], json.dumps(metrics)),
            )
            db.execute("DELETE FROM findings WHERE task_id = ?", (task_id,))
            db.executemany("INSERT OR IGNORE INTO findings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
                (task_id, f"{item['category']}:{item['file']}:{item['line']}", item["severity"], item["category"], item["file"],
                 item["line"], item["evidence"], item["recommendation"], item["confidence"])
                for item in report["findings"] + report.get("needs_human_review", [])
            ])
            db.execute("DELETE FROM filter_decisions WHERE task_id = ?", (task_id,))
            db.executemany("INSERT INTO filter_decisions VALUES (?, ?, ?)", [
                (task_id, item.get("decision", ""), item.get("reason", ""))
                for item in report.get("filter_decisions", [])
            ])
            db.execute("DELETE FROM skill_runs WHERE task_id = ?", (task_id,))
            db.executemany("INSERT INTO skill_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
                (task_id, item.get("runtime", "skill_run"), json.dumps(item.get("command", "")),
                 item.get("status", "passed"), item.get("exit_code"), item.get("stdout", ""), item.get("stderr", ""),
                 int(item.get("timed_out", False)), item.get("duration_seconds", 0),
                 json.dumps(item.get("output_files", []), ensure_ascii=False))
                for item in report.get("skill_runs", [])
            ])
            db.execute("DELETE FROM model_runs WHERE task_id = ?", (task_id,))
            db.executemany("INSERT INTO model_runs VALUES (?, ?, ?, ?)", [
                (task_id, item.get("model", ""), item.get("duration_seconds", 0),
                 json.dumps({"input": item.get("input", {}), "output": item.get("output", {}), "exception": item.get("exception", "")}, ensure_ascii=False))
                for item in report.get("model_runs", [])
            ])

    def get_task(self, task_id: str) -> dict:
        """Return the complete persisted audit trail for a single review task."""
        with self._connect() as db:
            task = db.execute("SELECT status, input_digest FROM review_tasks WHERE task_id = ?", (task_id,)).fetchone()
            if not task:
                raise KeyError(f"Unknown review task: {task_id}")
            runs = db.execute("SELECT runtime, status, exit_code, output FROM sandbox_runs WHERE task_id = ?", (task_id,)).fetchall()
            decisions = db.execute("SELECT decision, reason FROM filter_decisions WHERE task_id = ?", (task_id,)).fetchall()
            findings = db.execute("SELECT severity, category, file, line, evidence, recommendation, confidence FROM findings WHERE task_id = ?", (task_id,)).fetchall()
            metric = db.execute("SELECT finding_count, sandbox_run_count, blocked_count, metrics_json FROM review_metrics WHERE task_id = ?", (task_id,)).fetchone()
            skill_loads = db.execute("SELECT operation, documents_json, result_json FROM skill_loads WHERE task_id = ?", (task_id,)).fetchall()
            skill_runs = db.execute("SELECT runtime, command_json, status, exit_code, output, stderr, timed_out, duration_seconds, output_files_json FROM skill_runs WHERE task_id = ?", (task_id,)).fetchall()
            model_runs = db.execute("SELECT model, duration_seconds, result_json FROM model_runs WHERE task_id = ?", (task_id,)).fetchall()
            state_events = db.execute("SELECT from_state, to_state, reason, timestamp FROM review_state_events WHERE task_id = ? ORDER BY timestamp", (task_id,)).fetchall()
        return {
            "task_id": task_id,
            "status": task[0],
            "input_digest": task[1],
            "sandbox_runs": [dict(zip(("runtime", "status", "exit_code", "output"), row)) for row in runs],
            "filter_decisions": [dict(zip(("decision", "reason"), row)) for row in decisions],
            "findings": [dict(zip(("severity", "category", "file", "line", "evidence", "recommendation", "confidence"), row)) for row in findings],
            "metrics": (
                json.loads(metric[3]) if metric and metric[3]
                else dict(zip(("finding_count", "sandbox_run_count", "blocked_count"), metric[:3]))
                if metric else {"finding_count": 0, "sandbox_run_count": 0, "blocked_count": 0}
            ),
            "skill_loads": [{"operation": row[0], "documents": json.loads(row[1]), "result": json.loads(row[2])} for row in skill_loads],
            "skill_runs": [{"runtime": row[0], "command": json.loads(row[1]), "status": row[2], "exit_code": row[3], "output": row[4], "stderr": row[5], "timed_out": bool(row[6]), "duration_seconds": row[7], "output_files": json.loads(row[8])} for row in skill_runs],
            "model_runs": [{"model": row[0], "duration_seconds": row[1], "result": json.loads(row[2])} for row in model_runs],
            "state_events": [dict(zip(("from_state", "to_state", "reason", "timestamp"), row)) for row in state_events],
        }
