-- storage/schema.sql - Code Review Agent 七表 DDL（验收3 按task查 + 验收5 落库脱敏）

-- 版本迁移表（真迁移：记录已应用版本，支持 ALTER）
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- 任务表（review_tasks）：记录每次代码审查任务的元数据
CREATE TABLE IF NOT EXISTS review_tasks (
    task_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,                      -- running/completed/failed
    conclusion TEXT,                           -- approve/changes_requested/needs_human_review/completed_with_warnings/failed
    repository TEXT NOT NULL,
    scope TEXT NOT NULL,
    total_duration_ms INTEGER,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

-- 输入差异表（input_diffs）：记录 git diff 输入的摘要信息
CREATE TABLE IF NOT EXISTS input_diffs (
    task_id TEXT PRIMARY KEY REFERENCES review_tasks(task_id) ON DELETE CASCADE,
    digest TEXT,                               -- diff 摘要（SHA256）
    redacted_summary TEXT,                     -- 脱敏后的差异摘要
    files_json TEXT,                           -- 变更文件列表（JSON）
    line_count INTEGER                         -- 变更行数
);

-- 沙箱运行表（sandbox_runs）：记录沙箱执行脚本的输出（已脱敏）
CREATE TABLE IF NOT EXISTS sandbox_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES review_tasks(task_id) ON DELETE CASCADE,
    runtime TEXT NOT NULL,                     -- python/node/bash 等
    script TEXT NOT NULL,                      -- 执行的脚本内容
    status TEXT NOT NULL,                      -- success/failed/timeout/blocked
    exit_code INTEGER,
    stdout_redacted TEXT,                      -- 脱敏后的标准输出
    stderr_redacted TEXT,                      -- 脱敏后的标准错误
    truncated INTEGER NOT NULL DEFAULT 0,      -- 是否被截断（SQLite 无 BOOLEAN）
    error_type TEXT,                           -- 错误类型（TimeoutError/语法错误等）
    duration_ms INTEGER NOT NULL
);

-- 过滤决策表（filter_decisions）：记录各阶段的过滤决策（预提交/后提交等）
CREATE TABLE IF NOT EXISTS filter_decisions (
    decision_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES review_tasks(task_id) ON DELETE CASCADE,
    stage TEXT NOT NULL,                       -- pre_commit/post_commit/pre_merge 等
    decision TEXT NOT NULL,                    -- allow/deny/needs_human_review
    reason TEXT,                               -- 决策原因（已脱敏）
    command_redacted TEXT                      -- 执行的命令（已脱敏）
);

-- 发现表（findings）：记录代码问题发现（含 bucket 分桶）
-- UNIQUE 约束确保同一任务的同一问题不会重复插入（幂等 save）
CREATE TABLE IF NOT EXISTS findings (
    finding_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES review_tasks(task_id) ON DELETE CASCADE,
    bucket TEXT NOT NULL,                      -- findings/warnings/needs_human_review
    severity TEXT NOT NULL,                    -- critical/high/medium/low
    category TEXT NOT NULL,                    -- security/performance/style 等
    file TEXT NOT NULL,
    line INTEGER,
    title TEXT,                                -- 问题标题（已脱敏）
    evidence TEXT,                            -- 证据代码（已脱敏）
    recommendation TEXT,                       -- 修复建议（已脱敏）
    confidence REAL NOT NULL,
    source TEXT NOT NULL,                      -- rule/ast/sandbox/semgrep/llm/rule+llm
    rule_id TEXT NOT NULL,
    UNIQUE(task_id, bucket, file, line, category, rule_id)
);

-- 监控汇总表（monitoring_summaries）：记录任务执行的性能指标
CREATE TABLE IF NOT EXISTS monitoring_summaries (
    task_id TEXT PRIMARY KEY REFERENCES review_tasks(task_id) ON DELETE CASCADE,
    total_duration_ms INTEGER NOT NULL,
    sandbox_duration_ms INTEGER NOT NULL,
    tool_call_count INTEGER NOT NULL,
    blocked_count INTEGER NOT NULL DEFAULT 0,
    finding_count INTEGER NOT NULL,
    severity_distribution TEXT NOT NULL,        -- JSON: {"critical": 1, "high": 2}
    exception_distribution TEXT NOT NULL        -- JSON: {"TimeoutError": 1}
);

-- 审查报告表（review_reports）：记录最终生成的多格式报告
CREATE TABLE IF NOT EXISTS review_reports (
    task_id TEXT PRIMARY KEY REFERENCES review_tasks(task_id) ON DELETE CASCADE,
    report_json TEXT NOT NULL,                 -- JSON 格式报告
    report_md TEXT NOT NULL,                   -- Markdown 格式报告
    report_sarif TEXT                          -- SARIF 格式报告（可选）
);

-- 创建索引以优化查询性能
CREATE INDEX IF NOT EXISTS idx_sandbox_runs_task_id ON sandbox_runs(task_id);
CREATE INDEX IF NOT EXISTS idx_filter_decisions_task_id ON filter_decisions(task_id);
CREATE INDEX IF NOT EXISTS idx_findings_task_id ON findings(task_id);
CREATE INDEX IF NOT EXISTS idx_findings_bucket ON findings(bucket);
CREATE INDEX IF NOT EXISTS idx_review_tasks_status ON review_tasks(status);
