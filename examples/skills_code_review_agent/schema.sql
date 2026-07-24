PRAGMA foreign_keys = ON;

CREATE TABLE review_tasks (
  task_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  input_sha256 TEXT NOT NULL,
  input_summary_json TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE sandbox_runs (
  id INTEGER PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES review_tasks(task_id),
  command_json TEXT NOT NULL,
  status TEXT NOT NULL,
  exit_code INTEGER,
  duration_ms REAL NOT NULL,
  output_summary TEXT NOT NULL,
  error_type TEXT
);

CREATE TABLE filter_blocks (
  id INTEGER PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES review_tasks(task_id),
  decision TEXT NOT NULL,
  reason TEXT NOT NULL,
  command_json TEXT NOT NULL
);

CREATE TABLE findings (
  id INTEGER PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES review_tasks(task_id),
  severity TEXT NOT NULL,
  category TEXT NOT NULL,
  file TEXT NOT NULL,
  line INTEGER NOT NULL,
  title TEXT NOT NULL,
  evidence TEXT NOT NULL,
  recommendation TEXT NOT NULL,
  confidence REAL NOT NULL,
  source TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  is_warning INTEGER NOT NULL
);

CREATE TABLE reports (
  task_id TEXT PRIMARY KEY REFERENCES review_tasks(task_id),
  conclusion TEXT NOT NULL,
  report_json TEXT NOT NULL,
  monitoring_json TEXT NOT NULL
);
