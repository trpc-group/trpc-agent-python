# Design: Automated Code Review Agent

## Architecture

The agent follows a pipeline architecture with five stages:

```
Input(diff) -> Parser -> Rules -> Dedup+Redact -> Storage+Report
```

**Skill Design**: The code-review skill provides 4 rule categories (security, resource leaks, error handling, testing) and two sandbox scripts for independent execution. The ten deterministic regex rules guarantee baseline detection rates without LLM dependency, enabling dry-run mode that completes in under one second. Rules are confidence-weighted: critical findings default to 0.95 confidence while testing rules at 0.80 route to human-review warnings.

**Sandbox Isolation**: Script execution uses local subprocess executor with timeout control (30s default) and output size limits (100KB). Container executor interface is reserved for production deployment where network isolation and read-only mounts are required. Execution failures are captured as partial results without aborting the review pipeline.

**Filter Strategy**: Three-level pre-execution governance classifies commands into deny (system destruction like rm -rf, mkfs, fork bombs — blocked from sandbox entirely), ask (privilege escalation like sudo, chown — requires user confirmation), and needs_human_review (dependency installs like pip install, network calls like curl — flagged for human judgment). Denied findings are recorded in filter_decision table and excluded from the report. Review-flagged items remain present but with filter_action metadata for auditor visibility.

**Database Schema**: Six SQLite tables connected by task_id foreign keys form a complete audit trail: review_task for job metadata, finding for structured issues, sandbox_run for execution records, filter_decision for governance decisions, monitoring for performance metrics with JSON severity distribution, and report for generated outputs. The get_task_details method aggregates all records for a single task.

**Dedup and Noise Reduction**: Findings are deduplicated using (file, line, category) triplets as composite keys. Items with confidence below 0.85 are routed to warnings for human review rather than mixed with high-confidence findings. Testing violations and other inherently uncertain categories automatically fall into the warning bucket.

**Security Boundaries**: Sensitive data including API keys, access tokens, bearer credentials, JWT tokens, and passwords is redacted at multiple boundaries: finding evidence before database writes, sandbox stdout/stderr before persistence, and report content before output. Redaction combines seven regex pattern families with Shannon entropy detection for identifying unknown high-entropy credential strings.
