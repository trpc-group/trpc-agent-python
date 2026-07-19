# Skills Code Review Agent

This example implements a structured code-review agent prototype on top of
tRPC-Agent-Python. It combines deterministic rule detection, a formal
`code-review` skill package, filter-based governance, sandboxed script
execution, SQLite persistence, and dual-format reports.

## What This Example Covers

- Unified diff, repo path, and fixture inputs
- Deterministic review rules for:
  - security risks
  - async errors
  - resource leaks
  - missing tests
  - sensitive information leaks
  - database lifecycle issues
- Finding dedupe and confidence-based routing
- Filter decisions before sandbox execution
- Sandboxed skill-script execution with timeout and output truncation
- Secret redaction before reporting and persistence
- SQLite storage for tasks, inputs, findings, sandbox runs, filter decisions, and reports
- `review_report.json` and `review_report.md` output generation
- Dry-run and fake-model compatible execution

## Directory Map

- `agent/`: orchestration, config, and runtime helpers
- `skills/code-review/`: formal skill package and deterministic scripts
- `src/`: parser, rules, filter policy, storage, report, telemetry, redaction
- `tests/`: fixtures and automated tests
- `DEVELOPMENT_PLAN.md`: phased implementation plan
- `DESIGN_NOTE.md`: short solution design summary
- `ACCEPTANCE_CHECKLIST.md`: acceptance-standard mapping

## Main Flow

```text
CLI / input
  -> normalized diff input
  -> structured diff parsing
  -> deterministic rule engine
  -> dedupe and confidence routing
  -> filter decisions
  -> sandboxed skill scripts
  -> redaction
  -> JSON/Markdown reports
  -> SQLite persistence
```

## Running The Example

### 1. Run against a fixture

```bash
python examples/skills_code_review_agent/run_agent.py ^
  --fixture examples/skills_code_review_agent/tests/fixtures/security_issue.diff ^
  --output-dir examples/skills_code_review_agent/sample_outputs ^
  --db-path examples/skills_code_review_agent/sample_outputs/review.db ^
  --dry-run ^
  --fake-model
```

### 2. Run against a diff file

```bash
python examples/skills_code_review_agent/run_agent.py ^
  --diff-file path/to/change.diff ^
  --output-dir examples/skills_code_review_agent/sample_outputs ^
  --db-path examples/skills_code_review_agent/sample_outputs/review.db ^
  --dry-run ^
  --fake-model
```

### 3. Run against a repo path

```bash
python examples/skills_code_review_agent/run_agent.py ^
  --repo-path path/to/repo ^
  --output-dir examples/skills_code_review_agent/sample_outputs ^
  --db-path examples/skills_code_review_agent/sample_outputs/review.db ^
  --dry-run ^
  --fake-model
```

## Outputs

Each run produces:

- `review_report.json`
- `review_report.md`
- `review.db`
- staged diff inputs under `skill_inputs/`

The JSON report includes:

- final verdict
- findings
- warnings
- needs-human-review items
- filter decisions
- sandbox runs
- severity stats
- monitoring summary

## SQLite Query Surface

The repository layer persists these tables:

- `review_tasks`
- `review_inputs`
- `filter_decisions`
- `sandbox_runs`
- `findings`
- `review_reports`

You can fetch a full task bundle with:

- [ReviewRepository](file:///c:/Users/32349/trpc-agent-python-fork/examples/skills_code_review_agent/src/storage/repository.py)

Key method:

- `get_review_bundle(task_id)`

## Formal Skill Package

The reusable skill lives under:

- [SKILL.md](file:///c:/Users/32349/trpc-agent-python-fork/examples/skills_code_review_agent/skills/code-review/SKILL.md)
- [USAGE.md](file:///c:/Users/32349/trpc-agent-python-fork/examples/skills_code_review_agent/skills/code-review/USAGE.md)
- [RULES.md](file:///c:/Users/32349/trpc-agent-python-fork/examples/skills_code_review_agent/skills/code-review/RULES.md)
- [SCRIPT_CONTRACTS.md](file:///c:/Users/32349/trpc-agent-python-fork/examples/skills_code_review_agent/skills/code-review/SCRIPT_CONTRACTS.md)

The agent currently formalizes the skill package, its scripts, and its
`SkillToolSet` entrypoints. The main pipeline still owns orchestration,
governance, persistence, and final reporting.

## Test Coverage

Run the full example test suite with:

```bash
pytest examples/skills_code_review_agent/tests -q
```

Covered scenarios include:

- clean diff
- security issue
- async/resource leak
- database lifecycle issue
- missing tests
- duplicate finding behavior
- sandbox failure
- secret redaction

Phase 6 quality-gate evidence:

- full suite passes locally
- all 8 public fixtures generate both report artifacts
- a measured security fixture dry-run completes in about `9.87s`

## Related Docs

- [DEVELOPMENT_PLAN.md](file:///c:/Users/32349/trpc-agent-python-fork/examples/skills_code_review_agent/DEVELOPMENT_PLAN.md)
- [DESIGN_NOTE.md](file:///c:/Users/32349/trpc-agent-python-fork/examples/skills_code_review_agent/DESIGN_NOTE.md)
- [ACCEPTANCE_CHECKLIST.md](file:///c:/Users/32349/trpc-agent-python-fork/examples/skills_code_review_agent/ACCEPTANCE_CHECKLIST.md)
- [PR_READINESS.md](file:///c:/Users/32349/trpc-agent-python-fork/examples/skills_code_review_agent/PR_READINESS.md)
