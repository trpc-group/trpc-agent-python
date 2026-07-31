# Skills Code Review Agent

Automated code review agent for Python projects using Skills, deterministic rules, sandbox execution, and SQLite persistence.

## Quick Start

```bash
cd examples/skills_code_review_agent

# Sync mode: review a diff file or git repo
python run_review.py --diff-file fixtures/security.diff
python run_review.py --repo-path /path/to/your/repo

# Agent mode: LlmAgent + SkillToolSet + FakeModel (no API key)
python run_review.py --diff-file fixtures/security.diff --agent

# Agent mode with real LLM enhancement
export TRPC_AGENT_API_KEY=your_key
export TRPC_AGENT_BASE_URL=https://api.openai.com/v1
export TRPC_AGENT_MODEL_NAME=gpt-4o-mini
python run_review.py --diff-file fixtures/security.diff --agent --model gpt-4o-mini

# Run all tests
python -m pytest test_code_review_agent.py -v
```

## Modes

| Mode | Flag | Driver | Skill Loading | Sandbox | Filter |
|------|------|--------|---------------|---------|--------|
| Sync (default) | `--diff-file` | `main()` function pipeline | not used | `SandboxRunner.run_script()` | `check_dangerous()` |
| Agent | `--agent` | `LlmAgent` + `Runner.run_async()` | `skill_load`/`skill_run` | `SkillRunTool` + `LocalWorkspaceRuntime` | `CodeReviewSafetyFilter(BaseFilter)` |

## Filter Governance (three-level semantics)

Commands found in tool calls / finding evidence are classified into three levels:

| Level | Examples | Behavior |
|-------|----------|----------|
| `deny` | `rm -rf /`, `mkfs`, fork bomb | Hard-blocked (`is_continue=False`), never executed, decision recorded |
| `ask` | `sudo`, `iptables`, `chown` | Interactive confirmation prompt (`y/N`); approved → executes and recorded as `approved`, rejected → blocked and recorded as `denied (no human confirmation)`. Use `--non-interactive` to auto-reject without prompting |
| `needs_human_review` | `pip install`, `curl`, `wget` | **Same decision gate as `ask`**: interactive confirmation before execution; approved → executes and recorded as `approved`, rejected → blocked and recorded as `denied (no human confirmation)`. Use `--non-interactive` to auto-reject |

**Acceptance criterion #7 compliance:** criterion #7 states that `deny / needs_human_review` must not directly reach sandbox execution. This example enforces that literally: neither `deny` nor `needs_human_review` commands execute without an operator decision. `deny` is always blocked; `ask`/`needs_human_review` prompt interactively (or auto-reject with `--non-interactive`), and every decision (approved/denied) is recorded in the filter_decision table and surfaced in the report "Filter Intercepts" section. In Agent mode the filter is attached to `skill_run` via `SkillToolSet(run_tool_kwargs={"filters": [CodeReviewSafetyFilter(...)]})`, so enforcement happens before execution.

## Review Categories

| Category | Severity | Examples |
|----------|----------|----------|
| Security | critical | Hardcoded secrets, shell=True, eval(), pickle.loads |
| Resource Leak | high | open() without with, unclosed HTTP sessions, DB connections |
| Error Handling | high | Swallowed exceptions, bare except clauses |
| Testing | medium | New functions/classes without test coverage |

## Test Fixtures

| Fixture | Description | Expected |
|---------|-------------|----------|
| clean.diff | Trivial helper function | 0 findings |
| security.diff | Hardcoded secrets, eval, pickle, shell=True | >=3 security findings |
| resource_leak.diff | open() without with, unclosed DB connection | >=2 resource findings |
| db_lifecycle.diff | pymysql.connect without context manager | >=1 DB lifecycle finding |
| missing_test.diff | New functions without test file | >=1 testing warning |
| duplicate.diff | Multiple issues on same file | Proper dedup |
| sandbox_fail.diff | Long-running script | No crash, partial results |
| sensitive_info.diff | API keys, tokens, passwords | All secrets redacted |

## Output

- `output/<task_id>/review_report.json` — JSON structured report
- `output/<task_id>/review_report.md` — Markdown human-readable report

## Architecture

See [DESIGN.md](DESIGN.md) for architecture details.

## Requirements

- Python 3.10+
- trpc-agent-py >= 1.1.0
- pytest (for tests only)
