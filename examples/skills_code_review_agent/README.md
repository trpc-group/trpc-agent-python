# Skills Code Review Agent

[简体中文](README.zh_CN.md) | [Design note](DESIGN.md)

This example implements [Issue #92](https://github.com/trpc-group/trpc-agent-python/issues/92): an automatic code-review agent that combines a reusable Skill, isolated workspace execution, deterministic rules, Filter governance, SQLite persistence, OpenTelemetry spans, redaction, and auditable reports. The acceptance path is deterministic and does not require an LLM API key.

## Measured acceptance results

Run the checked-in labelled corpus to reproduce the current result:

```bash
python examples/skills_code_review_agent/evaluate.py \
  --markdown --fail-under \
  --out tmp/code-review-eval.json
```

| Metric | Issue #92 threshold | Current measurement |
| --- | ---: | ---: |
| High-risk detection rate | >= 80% | **100.0% (15/15)** |
| False-positive rate | <= 15% | **0.0% (0/18)** |
| Secret-redaction recall | >= 95% | **100.0% (30/30)** |
| False-redaction rate | informational | **0.0% (0/12)** |

The holdout corpus contains 18 diffs: 12 positive cases and 6 negative controls. Detection and false positives are scored only in the high-confidence `findings` bucket; the evaluator reports recall including warnings separately so moving findings to manual review cannot inflate the score. Matching requires the same file and category within two lines. The redaction corpus contains 30 labelled secrets and 12 benign values.

These numbers describe the committed deterministic corpus, not a claim about arbitrary repositories. Keep `--fail-under` in CI so a rule change cannot silently move below the acceptance thresholds.

## Architecture

```text
unified diff / PR patch / git worktree / fixture
                         |
                         v
                  input parser + hash
                    /            \
       unredacted in memory       redacted diff only
              |                         |
              v                         v
   deterministic rules        Filter decision (pre-exec)
              |                         |
       AST/hunk context         SDK workspace runtime
       suppression audit     container / cube / local fallback
              |                         |
              +------ sandbox script findings
                         |
                  dedupe + bucketing
             findings / warnings / human review
                         |
                    output redaction
                    /              \
          JSON + Markdown       SQLite task bundle

        OpenTelemetry spans wrap every pipeline stage.
```

The sandbox never receives the original secret-bearing diff. In-process rules inspect the original only in memory, immediately redact evidence, and redact the complete object graph again before reports or database rows are written.

## Layout

```text
examples/skills_code_review_agent/
├── README.md / README.zh_CN.md
├── DESIGN.md
├── .env.example
├── Dockerfile
├── run_agent.py
├── evaluate.py
├── schema.sql
├── agent/                       # orchestration, policy, runtimes, storage
├── evalset/
│   ├── holdout/                 # 18 labelled evaluation diffs
│   ├── labels.json
│   └── secrets_corpus.json
├── fixtures/                    # 8 public acceptance fixtures
├── scripts/                     # database initialization/query helpers
└── skills/code-review/
    ├── SKILL.md
    ├── rules/                    # six rule-category documents
    └── scripts/
        ├── parse_diff.py
        └── static_rules.py
```

## Quick start

From the repository root, install the development dependencies:

```bash
pip install -e ".[dev]"
```

Run a public fixture without model credentials or Docker:

```bash
python examples/skills_code_review_agent/run_agent.py \
  --fixture security_issue \
  --dry-run \
  --output-dir tmp/code-review-security \
  --db-path tmp/code-review-security/review.sqlite3
```

The command writes:

- `review_report.json` and `review_report.md`;
- the review task, sandbox runs, Filter intercepts, findings, metrics, and final report to SQLite;
- the generated `task_id` to stdout.

Query the complete persisted bundle:

```bash
python examples/skills_code_review_agent/run_agent.py \
  --db-path tmp/code-review-security/review.sqlite3 \
  --query-task-id <task_id>
```

Supported inputs are mutually exclusive:

- `--diff-file`: a unified diff or PR patch;
- `--repo-path`: unstaged/staged changes in a local Git worktree;
- `--path-list-file` together with `--repo-path`: only listed worktree paths;
- `--fixture`: a checked-in fixture name without `.diff`.

## Runtime modes

| Mode | Intended use | Isolation and requirements |
| --- | --- | --- |
| `container` | Default production path | tRPC-Agent `BaseWorkspaceRuntime`, Docker, network disabled, read-only Skill mount |
| `cube` | Remote production sandbox | Cube/E2B sandbox created with internet access disabled; fails closed if the installed client cannot enforce it |
| `local` | Explicit development fallback | Runs on the host; never selected as the production default |
| `dry-run-local` | Deterministic CI path | Selected by `--dry-run` or `--fake-model`; no model key required |

The workspace contract is identical across SDK runtimes: `skills/`, `work/inputs/`, `runs/`, and `out/`. Every command receives a timeout, an output-byte budget, and an allowlisted environment. A bounded capture process terminates the child as soon as stdout or stderr reaches its collection budget; only a size-checked, redacted envelope reaches the SDK. Timeout, startup, execution, output-limit, and artifact failures become recorded `sandbox_run` rows and manual-review items instead of crashing the whole review. Container and Cube runtimes must declare backend-enforced network isolation before a workspace is created.

Build the optional pinned review image:

```bash
docker build \
  -t trpc-agent-code-review:local \
  examples/skills_code_review_agent
export CODE_REVIEW_IMAGE=trpc-agent-code-review:local
```

On PowerShell, set the last variable with `$env:CODE_REVIEW_IMAGE = "trpc-agent-code-review:local"`. The Dockerfile copies no source tree, `.env`, credentials, or build context into the image.

Use `--allow-local-fallback` only for an explicitly accepted development fallback when Docker is unavailable. The recorded runtime remains visible in the report and database.

## Filter governance

Two entry points enforce the same policy:

- the deterministic CLI calls `ReviewExecutionFilter` before each sandbox request;
- the optional `LlmAgent` path registers `CodeReviewSandboxPolicyFilter` on `SkillRunTool`, so a model-driven tool call is rejected before its handler executes.

The same filter instance is attached to the lower-level one-shot `workspace_exec` path. Interactive `skill_exec` / session tools and direct artifact saving are omitted from this tool set. Both remaining execution entries share a runtime facade that kills the child at the byte budget, redacts bounded stdout/stderr and inline file names/content before returning them, and exposes no `start_program` session API. A model therefore cannot bypass `skill_run` governance by dropping down to direct workspace execution.

The policy scans every real argv element, joined argv, and the caller-supplied display text. It recursively checks declarative inputs/outputs, rejects `host://` and absolute host paths, and enforces network, timeout, and output budgets. Model-driven `skill_run` calls must provide an explicit bounded `outputs` manifest; legacy `output_files`, implicit `out/**` export, and raw artifact persistence fail closed. This prevents a harmless display label or nested input object from hiding a hostile request.

The deterministic review pipeline records every `deny` / `needs_human_review` decision in its report and SQLite task bundle. The optional `LlmAgent` helper has no report/task lifecycle of its own: pass a task-scoped `intercept_sink` to `create_agent(...)` (for example, one that calls `ReviewStore.add_filter_intercept`) when the embedding service needs the same persistence. The filter returns a structured refusal even when no sink is configured. Each tool set owns its filter instance, so concurrent reviews do not share a process-global event sink.

Each `create_agent(...)` call owns its Container/Cube runtime. Use the returned `CodeReviewAgent` as an async context manager or call `await agent.close()` in `finally`; this stops the backing container or remote sandbox immediately and is idempotent. Applications using the lazy module-level `root_agent` can call `await close_root_agent()` during service shutdown.

Inside an already-running event loop, create Cube agents with `await create_agent_async("cube")`. The synchronous lazy `root_agent` can use Cube only when initialized before that loop starts; an in-loop access fails closed with guidance instead of starting a partially managed remote sandbox.

If the CLI exposes the demonstration probe, enable it explicitly with `--demo-filter-intercept`; it must not pollute normal review metrics.

## Telemetry

Pipeline stages emit spans through `trpc_agent_sdk.telemetry.tracer`. With no provider configured, tracing is a safe no-op. A configured OpenTelemetry provider can export the root `code_review.review` span and child spans for input loading, diff parsing, sandbox execution, rules, context suppression, persistence, and reporting. Attributes include task/input identity, runtime, status, duration, timeout state, finding counts, and error type; secret evidence is never attached.

For a local demonstration, use `--telemetry-console` (the `opentelemetry-sdk` package is included in development dependencies):

```bash
python examples/skills_code_review_agent/run_agent.py \
  --fixture security_issue --dry-run --telemetry-console \
  --output-dir tmp/code-review-telemetry \
  --db-path tmp/code-review-telemetry/review.sqlite3
```

## Storage and audit replay

SQLite is the default backend behind the review-store interface. `schema.sql` is the single DDL source and contains:

- `review_task`;
- `sandbox_run`;
- `finding`;
- `filter_intercept`;
- `review_metric`;
- `review_report`;
- `schema_version`.

Initialize a fresh database and inspect it by task id:

```bash
python examples/skills_code_review_agent/scripts/init_db.py \
  --db-path tmp/code-review.sqlite3

python examples/skills_code_review_agent/scripts/query_review.py \
  --db-path tmp/code-review.sqlite3 \
  query <task_id> \
  --format table
```

Use `--format json` for machine-readable replay. A stable task id must not silently erase earlier audit rows; use only the store/CLI's explicit overwrite or new-attempt mechanism when replacement is intentional.

## Evaluation and tests

Run the threshold suite and the original acceptance suite:

```bash
python -m pytest \
  tests/examples/test_skills_code_review_agent.py \
  tests/examples/test_skills_code_review_agent_eval.py \
  tests/examples/test_skills_code_review_agent_filter.py \
  -q -s
```

`-s` avoids a known Windows pytest stdin-capture handle issue in subprocess-based tests; the same tests can run without it on unaffected platforms.

Score the public fixtures separately:

```bash
python examples/skills_code_review_agent/evaluate.py \
  --labels examples/skills_code_review_agent/fixtures/labels.json \
  --diffs examples/skills_code_review_agent/fixtures \
  --skip-redaction --markdown --fail-under
```

The eight public fixtures cover a benign change, security issues, asynchronous resource leakage, database lifecycle, missing tests, deduplication, sandbox failure, and secret redaction.

## Security notes

- Never put real credentials in `.env.example`, fixtures, Docker build arguments, or documentation. Inject secrets at runtime from a secret manager or process environment.
- The default container network is `none`; enabling network access requires an explicit policy decision.
- Only allowlisted environment names cross the sandbox boundary.
- Output caps apply to stdout, stderr, and collected artifacts. Truncation is recorded.
- Redaction covers diff summaries, script output, artifacts, findings, reports, telemetry-safe attributes, and database values.
- Local mode executes on the host and is therefore a development fallback, not an isolation boundary.

## Rule documentation

The rule catalogue is indexed at [`skills/code-review/rules/README.md`](skills/code-review/rules/README.md). Each category documents rule IDs, detection patterns, severity, confidence, remediation, and known false positives. Context suppressions are recorded in the report rather than silently discarded.
