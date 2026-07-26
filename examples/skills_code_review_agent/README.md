# Skills Code Review Agent

This example combines an Agent, a code-review Skill, a policy Filter, an
isolated workspace runtime, and SQL persistence. It accepts a unified diff,
file list, Git workspace, or one of the eight included fixtures. Every run
produces queryable JSON and Markdown reports.

The example does not modify framework production APIs. It reuses:

- `LlmAgent` and `Runner`;
- `SkillToolSet` and the filesystem Skill repository;
- Container and local workspace runtimes;
- `BaseFilter`;
- `SqlStorage` and SQLAlchemy metadata.

## Security model

Container is the default runtime and is created with `network_mode=none`.
The Agent can load the `code-review` Skill but cannot access generic
`skill_run`; execution goes through the fixed `review_skill_run` tool.

Before execution, the tool freezes and hashes:

- the exact Python argv;
- workspace-relative input, output, and working-directory paths;
- the environment allowlist;
- runtime and network policy;
- timeout and output budgets;
- normalized input bytes;
- the three staged Skill files.

The Filter commits its decisions before the sandbox handler is called.
`DENY` and `NEEDS_HUMAN_REVIEW` never execute. Filter failures fail closed.
All persisted text, reports, exception text, tool output, and structured
values pass through the same secret redactor.

Local execution is an unsafe development fallback. It is rejected unless:

```powershell
$env:TRPC_CODE_REVIEW_ALLOW_UNSAFE_LOCAL = "1"
```

Do not enable local runtime in production.

## Fake model

Fake mode is deterministic and requires no API key. It still runs a real
`LlmAgent` loop with these tool calls:

```text
skill_load(code-review)
review_skill_run()
```

Container:

```powershell
uv run python examples/skills_code_review_agent/run_agent.py run `
  --fixture security `
  --runtime container `
  --fake-model
```

Explicit local development fallback:

```powershell
$env:TRPC_CODE_REVIEW_ALLOW_UNSAFE_LOCAL = "1"
uv run python examples/skills_code_review_agent/run_agent.py run `
  --fixture security `
  --runtime local `
  --fake-model
```

## Real model

The real-model path uses the same tools, Filter, sandbox, and report
contracts. Any OpenAI-compatible endpoint can be supplied:

```powershell
$env:OPENAI_API_KEY = "<your-key>"
$env:OPENAI_BASE_URL = "https://api.example.invalid/v1"
$env:MODEL_NAME = "model-name"

uv run python examples/skills_code_review_agent/run_agent.py run `
  --diff-file change.diff `
  --runtime container
```

Credentials are never passed to the sandbox environment.

## Inputs

Unified diff:

```powershell
uv run python examples/skills_code_review_agent/run_agent.py run `
  --diff-file change.diff --fake-model
```

File list, one repository-relative UTF-8 path per line:

```powershell
uv run python examples/skills_code_review_agent/run_agent.py run `
  --file-list changed-files.txt --fake-model
```

Git workspace, staged changes only:

```powershell
uv run python examples/skills_code_review_agent/run_agent.py run `
  --repo-path . --staged --fake-model
```

Worktree changes only:

```powershell
uv run python examples/skills_code_review_agent/run_agent.py run `
  --repo-path . --worktree --fake-model
```

Revision range:

```powershell
uv run python examples/skills_code_review_agent/run_agent.py run `
  --repo-path . --base main --head HEAD --fake-model
```

`--dry-run` parses input, creates and audits the immutable plan, and writes a
report without calling the sandbox.

## Database

SQLite is the default:

```text
sqlite:///skills_code_review.db
```

Override it with `--db-url` or `TRPC_CODE_REVIEW_DB_URL`. Async SQLAlchemy
drivers are selected when the URL contains `+aiosqlite`, `+asyncpg`, or
`+aiomysql`; missing drivers fail immediately.

Initialize tables explicitly:

```powershell
uv run python examples/skills_code_review_agent/scripts/init_db.py `
  --db-url sqlite:///skills_code_review.db
```

Query a completed task:

```powershell
uv run python examples/skills_code_review_agent/run_agent.py show `
  --task-id <task-id>
```

## Reports

Each task writes isolated files:

```text
reports/<task-id>/review_report.json
reports/<task-id>/review_report.md
```

Reports contain task summary, findings, human-review warnings, Filter
decisions, sandbox records, exceptions, metrics, and final conclusion.

## Tests

Lightweight suite:

```powershell
uv run pytest tests/examples/skills_code_review_agent -q
```

Coverage:

```powershell
uv run pytest tests/examples/skills_code_review_agent `
  --cov=examples/skills_code_review_agent/agent `
  --cov-fail-under=85
```

Optional real Container integration:

```powershell
$env:TRPC_CODE_REVIEW_CONTAINER_TEST = "1"
uv run pytest tests/examples/skills_code_review_agent/test_acceptance.py -q
```

Python has no Go-style race detector. The storage tests use a thread barrier,
concurrent writes, and uniqueness-conflict merging as the race-equivalent
acceptance.
