CREATE TABLE IF NOT EXISTS public.review_tasks (
    task_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    status TEXT NOT NULL,
    repository TEXT NOT NULL,
    scope TEXT NOT NULL,
    conclusion TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS public.review_inputs (
    task_id TEXT PRIMARY KEY REFERENCES public.review_tasks(task_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    digest TEXT NOT NULL,
    review_profile TEXT NOT NULL DEFAULT 'legacy',
    file_count INTEGER NOT NULL,
    hunk_count INTEGER NOT NULL,
    added_lines INTEGER NOT NULL,
    removed_lines INTEGER NOT NULL,
    files_json JSONB NOT NULL,
    redacted_preview TEXT NOT NULL
);

ALTER TABLE public.review_inputs
    ADD COLUMN IF NOT EXISTS review_profile TEXT NOT NULL DEFAULT 'legacy';

CREATE TABLE IF NOT EXISTS public.sandbox_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES public.review_tasks(task_id) ON DELETE CASCADE,
    command TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_ms DOUBLE PRECISION NOT NULL,
    exit_code INTEGER,
    timed_out BOOLEAN NOT NULL,
    output_truncated BOOLEAN NOT NULL,
    stdout_summary TEXT NOT NULL,
    stderr_summary TEXT NOT NULL,
    error_type TEXT
);

CREATE TABLE IF NOT EXISTS public.filter_decisions (
    decision_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES public.review_tasks(task_id) ON DELETE CASCADE,
    command TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS public.findings (
    finding_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES public.review_tasks(task_id) ON DELETE CASCADE,
    bucket TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    file TEXT NOT NULL,
    line INTEGER,
    title TEXT NOT NULL,
    evidence TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    source TEXT NOT NULL,
    UNIQUE(task_id, bucket, file, line, category)
);

CREATE TABLE IF NOT EXISTS public.monitoring_summaries (
    task_id TEXT PRIMARY KEY REFERENCES public.review_tasks(task_id) ON DELETE CASCADE,
    total_duration_ms DOUBLE PRECISION NOT NULL,
    sandbox_duration_ms DOUBLE PRECISION NOT NULL,
    tool_call_count INTEGER NOT NULL,
    blocked_count INTEGER NOT NULL,
    finding_count INTEGER NOT NULL,
    severity_distribution_json JSONB NOT NULL,
    exception_distribution_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS public.review_reports (
    task_id TEXT PRIMARY KEY REFERENCES public.review_tasks(task_id) ON DELETE CASCADE,
    report_json JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_review_inputs_digest_profile
    ON public.review_inputs(digest, review_profile);
CREATE UNIQUE INDEX IF NOT EXISTS idx_findings_unique_issue
    ON public.findings(task_id, file, COALESCE(line, -1), category);
CREATE INDEX IF NOT EXISTS idx_sandbox_runs_task_id
    ON public.sandbox_runs(task_id);
CREATE INDEX IF NOT EXISTS idx_filter_decisions_task_id
    ON public.filter_decisions(task_id);
