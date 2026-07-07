-- Code Review Agent Database Schema
-- SQLite-compatible DDL

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
