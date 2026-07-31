# Automated Code Review Agent

This directory implements an automated code review (CR) agent prototype leveraging tRPC-Agent-Python's skill architecture, database persistence, and filter governance policies.

## Design Description

### 1. Skill Design
The Code Review Agent uses a dedicated skill called `code-review` located under `skills/code-review/`. It defines `SKILL.md` (which documents inputs, rules, and commands) and houses sandboxed execution scripts under `scripts/`. These scripts separate concerns into:
- `parse_diff.py`: Parses the raw unified diff or patch file into structured hunks and modified line information.
- `run_checks.py`: Evaluates static analysis and AST checks on the code diff against rules.

### 2. Sandbox Isolation Strategy
Sandbox execution runs static analysis scripts, parsers, and custom check rules on the target diff in an isolated environment. The framework supports Docker (`ContainerWorkspaceRuntime`) as the default sandbox, with a local workspace fallback (`LocalWorkspaceRuntime`) for testing/development. Code execution is constrained with timeouts, memory limits, and file quota limits.

### 3. Filter Strategy & Safety Boundaries
The `FilterGovernance` policy manager runs checks on all commands prior to execution inside the sandbox:
- **High-Risk Intercepts**: Matches commands against forbidden list (e.g., `rm -rf`, `curl`, `bash -i`). Short commands (e.g., `nc`) are checked with strict word boundary regex to prevent false positives (like blocking `async`).
- **Forbidden Paths**: Restricts access to sensitive system paths (e.g., `/etc`, `C:\Windows`).
- **Sensitive Information Redaction**: Redacts API keys, tokens, and passwords matching signature regexes from all findings evidence, reports, and database columns.

### 4. Deduplication & Noise Reduction
Duplicate findings targeting the same `(file, line, category)` are merged to avoid spamming reports. Low confidence findings are routed to `needs_human_review` (warnings) instead of the main report findings section, separating high-impact findings from minor hints.

### 5. Database Schema
The database uses SQLAlchemy mapped tables on SQLite (designed to swap easily to Postgres/MySQL):
- `review_tasks`: Tasks execution state and diff summary.
- `sandbox_runs`: Logs, statuses, and performance timings of sandbox scripts.
- `findings`: Structured, parsed findings metadata.
- `review_reports`: Serialized JSON and markdown formatted review reports.
- `filter_logs`: Detailed filter interception trace logs.

### 6. Monitoring & Auditing
Each run tracks: total duration, sandboxed execute duration, tool call count, block count, findings count, and distribution of severities/exceptions for operational auditing.

### 7. Advanced Highlights (Top-3 Features)
This prototype implements three outstanding engineering features:
- **High-Precision AST Parsing**: Upgraded from simple string matching to Python's built-in `ast` module parsing. It identifies node-level syntax patterns (such as key-value configurations or call blocks) when files exist in the workspace, while seamlessly falling back to diff regex matching when only the diff is present.
- **Git Pre-commit Hook Integration**: Provided a ready-to-use Git pre-commit script ([pre_commit_hook.sh](file:///d:/my_document/project/others/trpc-agent-python/examples/skills_code_review_agent/pre_commit_hook.sh)). Copying or linking this script into `.git/hooks/` automatically runs code review on staged changes and rejects the commit if `CRITICAL` or `HIGH` severity violations are found.
- **Rich CLI Tables with UTF-8 Fallback**: The Agent CLI outputs a visually polished panel and formatted table using `rich`. It includes a platform detection layer that automatically falls back to clean, plain text formatting on non-UTF-8 terminals (like classic Windows cmd/powershell), preventing encoding distortion while preserving visual clarity.

---

## Getting Started

### 1. Running the Agent Review Pipeline
To perform a code review on a diff file, run the following:
```bash
python -m examples.skills_code_review_agent.agent --diff-file examples/skills_code_review_agent/fixtures/fixture_security.diff
```

### 2. Running Automated Tests
Run pytest to verify the full suite of 8 test cases:
```bash
$env:PYTHONPATH="d:\my_document\project\others\trpc-agent-python"; pytest examples/skills_code_review_agent/test_agent.py
```
