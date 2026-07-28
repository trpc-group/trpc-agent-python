-- Reference SQLite schema. Runtime creation uses equivalent SQLAlchemy tables.
CREATE TABLE review_task (
  id TEXT PRIMARY KEY, repo_path TEXT, commit_hash TEXT, input_type TEXT NOT NULL,
  input_digest TEXT NOT NULL, diff_summary TEXT NOT NULL, status TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL, started_at TIMESTAMP, finished_at TIMESTAMP,
  error_type TEXT
);
CREATE TABLE skill_execution (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES review_task(id),
  skill_name TEXT NOT NULL, skill_version TEXT NOT NULL, rule_version TEXT NOT NULL,
  detector_type TEXT NOT NULL, started_at TIMESTAMP NOT NULL,
  finished_at TIMESTAMP NOT NULL, result_summary TEXT NOT NULL
);
CREATE TABLE sandbox_run (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES review_task(id),
  runtime TEXT NOT NULL, runtime_version TEXT NOT NULL, image TEXT,
  command TEXT NOT NULL, command_digest TEXT NOT NULL, status TEXT NOT NULL,
  exit_code INTEGER, timed_out BOOLEAN NOT NULL, stdout TEXT NOT NULL,
  stderr TEXT NOT NULL, duration FLOAT NOT NULL, created_at TIMESTAMP NOT NULL
);
CREATE TABLE filter_event (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES review_task(id),
  sandbox_run_id TEXT REFERENCES sandbox_run(id), action TEXT NOT NULL,
  command TEXT NOT NULL, command_digest TEXT NOT NULL, decision TEXT NOT NULL,
  risk_level TEXT NOT NULL, reason TEXT NOT NULL, reason_code TEXT NOT NULL,
  matched_rule TEXT NOT NULL, created_at TIMESTAMP NOT NULL
);
CREATE TABLE finding (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES review_task(id),
  skill_execution_id TEXT REFERENCES skill_execution(id),
  sandbox_run_id TEXT REFERENCES sandbox_run(id), severity TEXT NOT NULL,
  category TEXT NOT NULL, file TEXT NOT NULL, line INTEGER NOT NULL,
  title TEXT NOT NULL, evidence TEXT NOT NULL, recommendation TEXT NOT NULL,
  confidence FLOAT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL,
  needs_human_review BOOLEAN NOT NULL, dedupe_key TEXT NOT NULL,
  rule_id TEXT NOT NULL, rule_version TEXT NOT NULL,
  validation_status TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL, UNIQUE(task_id, file, line, category)
);
CREATE TABLE review_report (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES review_task(id),
  summary TEXT NOT NULL, report_json TEXT NOT NULL, report_markdown TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL
);
CREATE TABLE telemetry (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES review_task(id),
  total_duration FLOAT NOT NULL, sandbox_duration FLOAT NOT NULL,
  tool_calls INTEGER NOT NULL, filter_blocks INTEGER NOT NULL,
  finding_count INTEGER NOT NULL, error_count INTEGER NOT NULL,
  severity_distribution TEXT NOT NULL, error_distribution TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL
);
CREATE INDEX ix_review_task_status ON review_task(status);
CREATE INDEX ix_review_task_created_at ON review_task(created_at);
CREATE INDEX ix_finding_task_id ON finding(task_id);
CREATE INDEX ix_finding_severity ON finding(severity);
PRAGMA foreign_keys=ON;
