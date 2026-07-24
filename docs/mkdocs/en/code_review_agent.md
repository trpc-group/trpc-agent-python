# Code Review Agent Design

This page describes a proposed automatic code-review Agent that combines Agent Skills, sandboxed execution, structured findings, filters, telemetry, and SQL storage. It is a design and scaffold document: it defines the expected architecture and contracts before a production-ready implementation is added.

## Overview

The code-review Agent receives a unified diff, a PR patch, or local repository changes and produces a structured review report. A future runnable implementation should support inputs such as:

- `--diff-file`: read a saved unified diff or patch.
- `--repo-path`: read local working-tree changes from a repository.
- Test fixtures: run deterministic samples in dry-run or fake-model mode.

Expected outputs are:

- `review_report.json`: machine-readable findings, filter decisions, sandbox runs, and metrics.
- `review_report.md`: human-readable summary.
- SQL records for review tasks, input summaries, sandbox runs, findings, filter decisions, monitoring summaries, and final reports.

## Non-goals for the scaffold

The first contribution should stay small and reviewable. It should not claim to implement the full review bot.

- No GitHub or PR comment posting.
- No automatic file modification or fix application.
- No host execution of model-generated commands.
- No production scheduler, webhook server, or credentials management.
- No complete sandbox, database, or model loop implementation yet.

## Architecture

A complete implementation should follow this flow:

```text
diff / repo changes
  -> diff parser
  -> code-review Skill
  -> Filter governance
  -> sandboxed checks
  -> structured finding validation
  -> dedupe and noise filtering
  -> SQLite storage
  -> JSON/Markdown report
```

The design separates three concerns:

1. **Review policy** lives in the `code-review` Skill and reference docs.
2. **Execution safety** lives in sandbox and Filter layers.
3. **Auditability** lives in structured output, SQL storage, and monitoring summaries.

## Existing building blocks

The implementation should reuse existing tRPC-Agent patterns instead of inventing a parallel framework.

- Skills: [`trpc_agent_sdk/skills/__init__.py`](../../../trpc_agent_sdk/skills/__init__.py)
  - `SkillToolSet`
  - `create_default_skill_repository`
  - `SkillLoadTool`
  - `SkillRunTool`
- Skill examples:
  - [`examples/skills`](../../../examples/skills)
  - [`examples/skills_with_container`](../../../examples/skills_with_container)
  - [`examples/skills_with_cube`](../../../examples/skills_with_cube)
- Code execution:
  - [`BaseCodeExecutor`](../../../trpc_agent_sdk/code_executors/_base_code_executor.py)
  - [`ContainerCodeExecutor`](../../../trpc_agent_sdk/code_executors/container/_container_code_executor.py)
- Filter governance:
  - [`FilterABC`](../../../trpc_agent_sdk/abc/_filter.py)
  - [`FilterResult`](../../../trpc_agent_sdk/abc/_filter.py)
- SQL storage:
  - [`SqlStorage`](../../../trpc_agent_sdk/storage/_sql.py)
  - [`SqlKey`](../../../trpc_agent_sdk/storage/_sql.py)
  - [`SqlCondition`](../../../trpc_agent_sdk/storage/_sql.py)

## Skill design

The code-review Skill is the reusable review policy package. A first scaffold can live under the example directory:

```text
examples/code_review_agent/skills/code-review/
  SKILL.md
  references/
    finding_schema.md
    security_boundary.md
  scripts/
    # future static checks / diff helpers
```

`SKILL.md` should define the review workflow and require structured output. Future rule documents should cover at least four categories from the issue:

- Security risks.
- Async errors.
- Resource leaks.
- Missing tests.
- Sensitive information leaks.
- Database transaction or connection lifecycle issues.

The Skill should not instruct the model to modify files. It should ask the model to produce candidate findings, while downstream validation, deduplication, and governance decide which findings are kept.

## Structured finding schema

Every high-confidence finding should be structured. Minimum fields:

| Field | Description |
| --- | --- |
| `severity` | `info`, `low`, `medium`, `high`, or `critical`. |
| `category` | Example: `security`, `async`, `resource_leak`, `test_coverage`, `secrets`, `database_lifecycle`. |
| `file` | Repository-relative file path. |
| `line` | New-file line number from the diff. |
| `title` | One-line summary. |
| `evidence` | Concrete diff or code evidence. |
| `recommendation` | Actionable fix guidance. |
| `confidence` | `low`, `medium`, or `high`. |
| `source` | Example: `skill`, `sandbox`, `filter`, or `fake_model`. |

Useful future fields:

- `fingerprint`: stable dedupe key.
- `line_start` / `line_end`: line span.
- `needs_human_review`: whether the result must be manually checked before promotion.
- `raw_source`: optional trace for debugging.

Low-confidence or weakly evidenced findings should be stored as warnings or `needs_human_review`; they should not be mixed into high-confidence findings.

## Diff parsing contract

The diff parser should support unified diffs from saved files, PR patches, or local `git diff` output. It should produce a compact, model-friendly representation while preserving enough structure for line anchoring.

Recommended internal shape:

```text
DiffFile
  old_path
  new_path
  status
  hunks[]

DiffHunk
  old_start
  old_count
  new_start
  new_count
  changed_lines[]

ChangedLine
  old_line_number
  new_line_number
  kind: added | removed | context
  text
```

Rules:

- Findings should anchor to new-file changed lines whenever possible.
- Deleted-only lines should not be used as final review comment anchors.
- Renames should preserve both old and new paths.
- Binary diffs should be summarized as unsupported instead of sent as raw binary content.

## Sandbox policy

The production path should be container-first or Cube/E2B-first. Local execution is useful for trusted development but should not be the default for untrusted diffs or model-generated commands.

Sandbox requirements:

- Mount repository inputs read-only.
- Write outputs only under a controlled workspace/output directory.
- Enforce command timeout.
- Enforce output-size limits.
- Pass only allowlisted environment variables.
- Do not pass secrets into the sandbox.
- Redact sensitive values from stdout, stderr, reports, and SQL records.
- Record failures without crashing the entire review task.

The container Skill example in `examples/skills_with_container` is the closest starting point for this path.

## Filter governance

Filters should act as policy gates before and after sandbox/model work.

Pre-execution checks should deny or mark `needs_human_review` for:

- High-risk scripts or commands.
- Forbidden repository paths.
- Non-allowlisted network access.
- Over-budget execution.
- Requests that require secrets unavailable in dry-run mode.

Post-processing checks should:

- Validate finding schema.
- Drop findings not anchored to changed lines.
- Deduplicate repeated findings.
- Downgrade low-confidence or speculative findings.
- Redact secrets.

Every filter decision should be written to the report and database, including the decision, reason, and filter name.

## SQLite schema proposal

A minimal SQLite-backed implementation can use the existing SQL storage patterns. Suggested tables:

### `review_tasks`

- `id`
- `repo_path`
- `base_ref`
- `head_ref`
- `mode`: `dry_run` or `apply`
- `status`
- `created_at`
- `completed_at`
- `metadata_json`

### `review_input_summaries`

- `id`
- `task_id`
- `diff_sha256`
- `file_count`
- `hunk_count`
- `changed_line_count`
- `summary_json`

### `sandbox_runs`

- `id`
- `task_id`
- `runtime`: `container`, `cube`, or `local_dev`
- `command`
- `exit_code`
- `duration_ms`
- `stdout_excerpt`
- `stderr_excerpt`
- `status`

### `review_findings`

- `id`
- `task_id`
- `fingerprint`
- `severity`
- `category`
- `file`
- `line_start`
- `line_end`
- `title`
- `evidence`
- `recommendation`
- `confidence`
- `source`

### `filter_decisions`

- `id`
- `task_id`
- `finding_fingerprint`
- `filter_name`
- `decision`: `allow`, `deny`, `drop`, `merge`, `downgrade`, or `needs_human_review`
- `reason`
- `created_at`

### `review_reports`

- `id`
- `task_id`
- `json_path`
- `markdown_path`
- `summary`
- `created_at`

### `monitoring_summaries`

- `id`
- `task_id`
- `total_duration_ms`
- `sandbox_duration_ms`
- `tool_call_count`
- `filter_interception_count`
- `finding_count`
- `severity_distribution_json`
- `exception_distribution_json`

## Dedupe and noise control

A stable finding fingerprint should include:

- normalized file path;
- line span or nearest changed-line anchor;
- category;
- normalized title/evidence;
- optional diff hunk hash.

The review pipeline should not surface duplicates for the same file, line, and category. Low-confidence issues should be kept in warnings or human-review sections instead of promoted to high-confidence findings.

## Monitoring and audit

Reports should include:

- total review duration;
- sandbox execution duration;
- tool call count;
- filter interception count;
- finding count;
- severity distribution;
- exception type distribution;
- sandbox failures and timeouts;
- filter deny / human-review decisions.

Audit events should record important lifecycle steps such as diff collection, skill loading, sandbox command requested, command allowed/denied, structured parsing success/failure, database write success/failure, and report rendering.

## Dry-run and fake-model mode

Dry-run should be the default. It should:

- parse the diff;
- load the Skill policy;
- exercise sandbox policy decisions when configured;
- validate and filter findings;
- write only local artifacts/database records;
- render reports;
- never post external comments;
- never modify repository files.

Fake-model mode should make the pipeline testable without real model API keys. It can return deterministic findings from fixtures to verify parsing, storage, filtering, and report rendering.

## Future implementation phases

1. **Docs and scaffold**: design docs, example README, and `code-review` Skill scaffold.
2. **Parser and schema**: unified diff parser, Pydantic finding models, fake-model path.
3. **Sandbox and storage**: container-first sandbox runner and SQLite persistence.
4. **Filters and metrics**: dedupe, redaction, human-review routing, monitoring summaries, and 8 fixture tests.
5. **Optional integrations**: PR comments, CI entrypoint, dashboards, or Cube/E2B remote sandbox configuration.
