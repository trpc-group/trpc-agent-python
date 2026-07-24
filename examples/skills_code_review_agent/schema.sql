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
