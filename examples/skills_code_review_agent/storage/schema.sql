-- ReviewMind Database Schema
--
-- 5 tables for the code review agent:
--   review_tasks   - Review task records
--   sandbox_runs   - Sandbox execution records
--   findings       - Code review findings
--   review_reports - Review report storage
--   filter_logs    - Filter interception logs
--   monitor_summary - Monitoring metrics

-- 1. 审查任务表
CREATE TABLE IF NOT EXISTS review_tasks (
    id              TEXT PRIMARY KEY,
    input_type      TEXT NOT NULL DEFAULT 'diff_file',
    input_summary   TEXT,                          -- JSON: {files: [...], total_additions: N, total_deletions: N}
    status          TEXT NOT NULL DEFAULT 'pending', -- pending | running | completed | failed
    total_duration_ms REAL DEFAULT 0,
    finding_count   INTEGER DEFAULT 0,
    severity_distribution TEXT,                     -- JSON: {"critical": N, "warning": N, "suggestion": N}
    error_message   TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. 沙箱执行记录表
CREATE TABLE IF NOT EXISTS sandbox_runs (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES review_tasks(id),
    script_name     TEXT NOT NULL,
    status          TEXT NOT NULL,                  -- success | timeout | failed | intercepted
    duration_ms     REAL DEFAULT 0,
    output_size_bytes INTEGER DEFAULT 0,
    exit_code       INTEGER,
    error_message   TEXT,
    intercept_reason TEXT,                          -- Filter 拦截原因 (仅 status='intercepted' 时)
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. 审查发现表
CREATE TABLE IF NOT EXISTS findings (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES review_tasks(id),
    severity        TEXT NOT NULL,                  -- critical | warning | suggestion
    category        TEXT NOT NULL,                  -- security | async | resource_leak | db | secret | test
    file_path       TEXT NOT NULL,
    line_number     INTEGER DEFAULT 0,
    title           TEXT NOT NULL,
    evidence        TEXT,                           -- 问题代码片段
    recommendation  TEXT,                           -- 修复建议
    confidence      TEXT NOT NULL DEFAULT 'medium', -- high | medium | low
    source          TEXT NOT NULL,                  -- static_check | pattern_match | llm
    dedup_key       TEXT,                           -- file_path:line_number:category 用于去重
    is_duplicate    INTEGER DEFAULT 0,              -- boolean
    needs_human_review INTEGER DEFAULT 0,           -- boolean
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. 审查报告表
CREATE TABLE IF NOT EXISTS review_reports (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES review_tasks(id),
    report_type     TEXT NOT NULL,                  -- json | markdown
    content         TEXT NOT NULL,                  -- 完整报告内容
    summary         TEXT,                           -- 简要摘要
    filter_intercept_summary TEXT,                  -- JSON: Filter 拦截摘要
    monitoring_metrics TEXT,                        -- JSON: 监控指标
    sandbox_exec_summary TEXT,                      -- JSON: 沙箱执行摘要
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5. Filter 拦截日志表
CREATE TABLE IF NOT EXISTS filter_logs (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES review_tasks(id),
    filter_type     TEXT NOT NULL,                  -- sandbox | secret | network | budget
    action          TEXT NOT NULL,                  -- allow | deny | needs_human_review
    target          TEXT,                           -- 被拦截的目标描述
    reason          TEXT,                           -- 拦截原因
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 6. 监控审计摘要表
CREATE TABLE IF NOT EXISTS monitor_summary (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES review_tasks(id),
    total_duration_ms REAL DEFAULT 0,
    sandbox_duration_ms REAL DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    intercept_count INTEGER DEFAULT 0,
    finding_count   INTEGER DEFAULT 0,
    severity_distribution TEXT,                     -- JSON
    exception_types TEXT,                           -- JSON list
    filter_intercepts TEXT,                         -- JSON list
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_findings_task_id ON findings(task_id);
CREATE INDEX IF NOT EXISTS idx_findings_dedup ON findings(dedup_key);
CREATE INDEX IF NOT EXISTS idx_sandbox_runs_task_id ON sandbox_runs(task_id);
CREATE INDEX IF NOT EXISTS idx_reports_task_id ON review_reports(task_id);
CREATE INDEX IF NOT EXISTS idx_filter_logs_task_id ON filter_logs(task_id);
CREATE INDEX IF NOT EXISTS idx_monitor_task_id ON monitor_summary(task_id);