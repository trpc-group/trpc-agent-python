# Code Review Agent Example

This example demonstrates a Skill-first automatic code-review Agent prototype with a deterministic dry-run path. It can parse unified diffs or a local git working tree diff, run deterministic review rules, apply pre-sandbox Filter governance, simulate sandbox execution, redact sensitive values, persist results to SQLite, and render JSON/Markdown reports.

The default mode intentionally does **not** require API keys, Docker, Cube/E2B, or network access. It uses a fake model and fake sandbox so tests can exercise the whole review pipeline in CI.

## What this example demonstrates

- A `code-review` Skill policy package under `skills/code-review/`.
- Unified diff parsing for files, hunks, changed lines, and line anchors.
- Deterministic rules for secrets, security risks, async issues, resource leaks, database lifecycle issues, and missing tests.
- Pre-sandbox governance decisions for allowlisted scripts, forbidden paths, risky commands, network access, and output budgets.
- Fake sandbox runs with failure/timeout/output-limit records.
- Structured findings, warnings, filter decisions, sandbox summaries, audit events, and metrics.
- Optional SQLite persistence for review task, sandbox run, filter decision, finding, warning, audit, and report records.

## Architecture

```text
user / CI request
  -> load --diff-file or --repo-path git diff
  -> parse files, hunks, changed lines, and anchors
  -> build sandbox requests
  -> apply pre-sandbox Filter governance
  -> execute allowed requests in fake sandbox
  -> run deterministic fake-model rules
  -> post-filter findings: redact, anchor, dedupe, route low confidence
  -> build review_report.json and review_report.md
  -> optionally persist task/report rows to SQLite
```

## Folder layout

```text
examples/code_review_agent/
  README.md
  review_report.json
  review_report.md
  run_review.py
  agent/
    diff_parser.py
    fake_reviewer.py
    filters.py
    governance.py
    inputs.py
    pipeline.py
    report.py
    rules.py
    sandbox.py
    schemas.py
    storage.py
  fixtures/
    clean.diff
    hardcoded_secret.diff
    async_resource_leak.diff
    db_lifecycle.diff
    missing_tests.diff
    duplicate_secret.diff
    sandbox_failure.diff
    sensitive_redaction.diff
    binary.diff
    removed_only.diff
    renamed_file.diff
  skills/code-review/
    SKILL.md
    scripts/
      diff_summary.py
      static_rules.py
    references/
      finding_schema.md
      rules.md
      sandbox_policy.md
      security_boundary.md
```

## Run the dry-run prototype

Review a fixture and print Markdown:

```bash
python examples/code_review_agent/run_review.py \
  --diff-file examples/code_review_agent/fixtures/hardcoded_secret.diff \
  --fake-model \
  --sandbox-runtime fake \
  --markdown
```

Write both report files and persist to SQLite:

```bash
python examples/code_review_agent/run_review.py \
  --diff-file examples/code_review_agent/fixtures/hardcoded_secret.diff \
  --fake-model \
  --sandbox-runtime fake \
  --db-path /tmp/code-review-agent.sqlite \
  --output-dir /tmp/code-review-agent-output
```

Expected output files:

```text
/tmp/code-review-agent-output/review_report.json
/tmp/code-review-agent-output/review_report.md
```

Print JSON for automation:

```bash
python examples/code_review_agent/run_review.py \
  --diff-file examples/code_review_agent/fixtures/clean.diff \
  --fake-model \
  --json
```

Review a local git working tree diff:

```bash
python examples/code_review_agent/run_review.py \
  --repo-path . \
  --fake-model \
  --sandbox-runtime fake \
  --json
```

Query a persisted task:

```bash
python examples/code_review_agent/run_review.py \
  --db-path /tmp/code-review-agent.sqlite \
  --show-task review-xxxxxxxxxxxx \
  --json
```

Fail when high-confidence findings are present:

```bash
python examples/code_review_agent/run_review.py \
  --diff-file examples/code_review_agent/fixtures/hardcoded_secret.diff \
  --fake-model \
  --fail-on-findings
```

## Fixture matrix

| Fixture | Purpose |
| --- | --- |
| `clean.diff` | No high-confidence finding. |
| `hardcoded_secret.diff` | Security/sensitive information leak. |
| `async_resource_leak.diff` | Untracked async task. |
| `db_lifecycle.diff` | Database connection lifecycle issue. |
| `missing_tests.diff` | Production change without a test update; warning/human review. |
| `duplicate_secret.diff` | Duplicate finding merge. |
| `sandbox_failure.diff` | Sandbox failure is recorded without crashing the review. |
| `sensitive_redaction.diff` | Multiple secret forms are redacted in reports and storage. |

Additional parser regression fixtures cover binary diffs, removed-only secrets, and renamed files.

## Skill package

The `code-review` Skill defines review scope, output fields, safety rules, and allowed script policy. In this deterministic prototype, the Python pipeline uses the same rule categories directly instead of invoking a real model loop with `skill_load` / `skill_run`. A production implementation can load the Skill before review, request only allowlisted scripts, and execute those scripts through Container or Cube/E2B workspace runtimes.

## SQLite storage

When `--db-path` is provided, the example creates these tables:

- `review_tasks`
- `sandbox_runs`
- `filter_decisions`
- `findings`
- `review_reports`
- `audit_events`

Rows store redacted summaries and report payloads. Raw secret-bearing diffs are not stored; the task stores a redacted diff hash and summary instead.

## Security boundaries

- Fake sandbox is the default and never executes host commands.
- Non-fake runtimes are not required for tests and should be treated as future adapters.
- Local execution is a development fallback only, not the production default.
- Pre-sandbox governance denies risky commands, forbidden paths, network access, and over-budget requests.
- Denied or `needs_human_review` sandbox requests are recorded but not executed.
- Reports and database rows redact API keys, tokens, passwords, private keys, cookies, authorization headers, and credential URLs.
- Sandbox failures, timeouts, and truncated output are recorded as non-fatal audit data.

## Design note

The prototype keeps the Agent implementation deterministic so contributors can validate the full review lifecycle without external services. The Skill package defines the policy layer: scope, review categories, finding schema, and sandbox safety expectations. The Python pipeline then implements that policy in a dry-run form. Inputs are normalized from either a diff file or local git diff, then parsed into files, hunks, and changed-line anchors. Before any executable check, sandbox requests pass through a governance filter that allowlists known scripts and rejects risky commands, forbidden paths, network access, or excessive output. The fake sandbox records the same shape of result that a Container or Cube/E2B runtime would provide, including failures and timeouts, but never executes arbitrary host commands. Deterministic rules produce structured findings for secrets, security risks, async issues, resource leaks, database lifecycle problems, and missing tests. Post-filters redact sensitive values, dedupe by file/line/category, and route low-confidence or unanchored issues to human-review warnings. SQLite persistence stores task state, sandbox runs, filter decisions, findings, warnings, metrics, audit events, and the final report using only redacted data. Reports expose findings, severity/category statistics, human-review items, Filter decisions, sandbox summaries, monitoring fields, and actionable recommendations.

## Verification

Run focused tests:

```bash
python -m pytest tests/examples/code_review_agent
```

Run existing Skill tests:

```bash
python -m pytest tests/skills
```

---

# 中文说明

这个示例实现了一个确定性的代码评审 Agent 原型：读取 diff 或本地 git diff，执行 fake model 规则、Filter 治理、fake sandbox、敏感信息脱敏、SQLite 落库，并生成 JSON / Markdown 报告。默认不需要 API key、Docker、Cube/E2B 或网络访问，适合本地和 CI 测试。生产实现可以在这个结构上替换真实模型和 Container / Cube/E2B workspace runtime。
