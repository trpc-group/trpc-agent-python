-- Code Review Agent — schema (Phase 0)
-- Seven tables aggregated around `review_task`; `task_id` is the global FK.
-- All statements use `IF NOT EXISTS` so the script is idempotent.
-- See ../docs/skills_code_review_agent/ARCHITECTURE.md §5.2 for the contract.

-- L6 master table: one row per review run.
CREATE TABLE IF NOT EXISTS review_task (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL,           -- pending|running|done|failed
  input_type TEXT NOT NULL,       -- diff|repo|fixture
  input_ref TEXT,
  mode TEXT NOT NULL,             -- dry-run|real
  total_duration_ms INTEGER
);

-- L1 parsed diff per changed file (summary only, never the full diff blob).
CREATE TABLE IF NOT EXISTS input_diff (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES review_task(id),
  file_path TEXT,
  sha256 TEXT,
  hunk_count INTEGER,
  line_count INTEGER,
  summary TEXT
);
CREATE INDEX IF NOT EXISTS idx_input_diff_task ON input_diff(task_id);

-- L4 sandbox execution evidence (replayable / auditable).
CREATE TABLE IF NOT EXISTS sandbox_run (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES review_task(id),
  runtime TEXT,                   -- local|container|cube
  script TEXT,
  status TEXT,                    -- ok|timeout|failed|truncated
  duration_ms INTEGER,
  exit_code INTEGER,
  output_bytes INTEGER,
  timed_out INTEGER,              -- 0|1 (SQLite has no native bool)
  masked_count INTEGER
);
CREATE INDEX IF NOT EXISTS idx_sandbox_run_task ON sandbox_run(task_id);

-- L5 structured findings (bucket enforces confidence tiering at schema level).
CREATE TABLE IF NOT EXISTS finding (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES review_task(id),
  severity TEXT,                  -- critical|high|medium|low
  category TEXT,                  -- security|async|resource|tests|sensitive|db
  file TEXT,
  line INTEGER,
  title TEXT,
  evidence TEXT,
  recommendation TEXT,
  confidence REAL,                -- 0.0-1.0
  source TEXT,                    -- rule|sandbox|llm
  bucket TEXT                     -- findings|warnings|needs_human_review
);
-- Composite index directly serves L5 dedupe lookup: O(1) "same file/line/category".
CREATE INDEX IF NOT EXISTS idx_finding_dedup ON finding(task_id, file, line, category);
CREATE INDEX IF NOT EXISTS idx_finding_task ON finding(task_id);

-- L3 filter governance blocks (deny / needs_human_review do not enter sandbox).
CREATE TABLE IF NOT EXISTS filter_block (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES review_task(id),
  reason TEXT,                    -- high-risk|forbidden-path|network|budget
  target TEXT,
  decision TEXT,                  -- deny|needs_human_review
  detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_filter_block_task ON filter_block(task_id);

-- Per-task telemetry rollup (severity flattened to columns for indexed reads).
CREATE TABLE IF NOT EXISTS monitor_summary (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES review_task(id),
  total_duration_ms INTEGER,
  sandbox_duration_ms INTEGER,
  tool_calls INTEGER,
  blocks INTEGER,
  finding_count INTEGER,
  sev_critical INTEGER,
  sev_high INTEGER,
  sev_medium INTEGER,
  sev_low INTEGER,
  exception_types TEXT            -- JSON, e.g. {"timeout":2,"oom":1}
);
CREATE INDEX IF NOT EXISTS idx_monitor_summary_task ON monitor_summary(task_id);

-- Final report artefact paths + human-readable summary.
CREATE TABLE IF NOT EXISTS review_report (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES review_task(id),
  report_json_path TEXT,
  report_md_path TEXT,
  summary TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_review_report_task ON review_report(task_id);
