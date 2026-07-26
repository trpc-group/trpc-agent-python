# Skills Code Review Agent

This example implements Issue #92 as a standalone, policy-gated code review
workflow. It accepts a unified diff, a file list, a Git workspace, or one of
eight fixtures and writes queryable JSON/Markdown reports plus SQLite audit
records. It does not change framework production APIs.

## What it includes

- a `code-review` Skill with deterministic rules and a sandbox script;
- unified diff, file-list, fixture, and Git input adapters;
- structured findings with deduplication and confidence routing;
- a Filter for command, path, environment, network, and budget policy;
- Container execution by default and an explicit local development fallback;
- SQLite-backed task, Filter, sandbox, finding, and report records;
- fake-model and dry-run modes that require no model API key.

## Quick start

Docker must be available for the default Container runtime:

```powershell
uv run python examples/skills_code_review_agent/run_agent.py run `
  --fixture security `
  --runtime container `
  --fake-model
```

The command prints the task ID and the two report paths:

```text
reports/<task-id>/review_report.json
reports/<task-id>/review_report.md
```

Use `--dry-run` to parse, filter, audit, and generate a report without invoking
the sandbox:

```powershell
uv run python examples/skills_code_review_agent/run_agent.py run `
  --fixture clean `
  --dry-run
```

## Input modes

Exactly one input option is required:

```powershell
# Unified diff
uv run python examples/skills_code_review_agent/run_agent.py run `
  --diff-file change.diff --fake-model

# UTF-8 file list; paths are relative to the list file's directory
uv run python examples/skills_code_review_agent/run_agent.py run `
  --file-list changed-files.txt --fake-model

# Staged Git changes
uv run python examples/skills_code_review_agent/run_agent.py run `
  --repo-path . --staged --fake-model

# Worktree-only Git changes
uv run python examples/skills_code_review_agent/run_agent.py run `
  --repo-path . --worktree --fake-model

# Revision range
uv run python examples/skills_code_review_agent/run_agent.py run `
  --repo-path . --base main --head HEAD --fake-model
```

With `--repo-path` and no range option, the input is `git diff HEAD`. Untracked
files are not included.

## Runtime security

Container is the production default and starts with Docker
`network_mode=none`. Only the fixed Skill files and normalized review input are
staged. The Filter audits the immutable execution plan before the sandbox is
called; denied or human-review decisions do not execute.

Local execution is an unsafe development fallback and is rejected unless it is
explicitly enabled:

```powershell
$env:TRPC_CODE_REVIEW_ALLOW_UNSAFE_LOCAL = "1"
uv run python examples/skills_code_review_agent/run_agent.py run `
  --fixture security `
  --runtime local `
  --fake-model
```

Do not enable the local runtime in production.

## Real model

The real-model path uses the same Skill, Filter, sandbox, storage, and report
contracts. Configure an OpenAI-compatible endpoint:

```powershell
$env:OPENAI_API_KEY = "<your-key>"
$env:OPENAI_BASE_URL = "https://api.example.invalid/v1"
$env:MODEL_NAME = "model-name"

uv run python examples/skills_code_review_agent/run_agent.py run `
  --diff-file change.diff `
  --runtime container
```

Model credentials are not included in the sandbox environment.

## Database and query

The default database is:

```text
sqlite:///skills_code_review.db
```

Override it with `--db-url` or `TRPC_CODE_REVIEW_DB_URL`. Initialize tables
without running a review:

```powershell
uv run python examples/skills_code_review_agent/scripts/init_db.py `
  --db-url sqlite:///skills_code_review.db
```

Query a task and its persisted report:

```powershell
uv run python examples/skills_code_review_agent/run_agent.py show `
  --task-id <task-id>
```

The report contains findings, warnings requiring human review, Filter
decisions, sandbox summaries, exceptions, metrics, and the final conclusion.
Sensitive text is redacted before report or database persistence.

## Tests

Run the lightweight suite:

```powershell
uv run pytest tests/examples/skills_code_review_agent -q
```

Run the coverage gate:

```powershell
uv run pytest tests/examples/skills_code_review_agent `
  --cov=examples.skills_code_review_agent `
  --cov-report=term-missing `
  --cov-fail-under=90
```

Run the optional real Container integration:

```powershell
$env:TRPC_CODE_REVIEW_CONTAINER_TEST = "1"
uv run pytest `
  tests/examples/skills_code_review_agent/test_acceptance.py `
  -k container -q
```

Without that environment variable, only the Container integration test is
skipped. SQLite concurrency tests use simultaneous writes and unique-key merge
handling; Python has no Go-style race detector.
