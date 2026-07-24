# Issue #91: Auditable evaluation and prompt optimization loop

This example demonstrates an evaluation-first prompt-optimization loop around the public `AgentOptimizer` API.  It keeps the checked-in fake and trace paths deterministic and credential-free, while live mode is an opt-in adapter with independent acceptance checks.

## Run

From the repository root:

```text
python examples/optimization/eval_optimize_loop/run_pipeline.py --mode fake --output-dir examples/optimization/eval_optimize_loop/sample_output
python examples/optimization/eval_optimize_loop/run_pipeline.py --mode trace --output-dir .tmp/issue-91-trace
python examples/optimization/eval_optimize_loop/run_pipeline.py --mode live --output-dir .tmp/issue-91-live
```

`fake` uses checked-in candidates and a local rubric; it needs no key and makes no network calls. `trace` evaluates the checked-in recorded conversation only. `live` validates `TRPC_AGENT_API_KEY`, `TRPC_AGENT_BASE_URL`, and `TRPC_AGENT_MODEL_NAME` before constructing an optimizer. It exits with code `2` if any are absent, and does not start network work in that case.

Live mode writes candidates to a temporary `TargetPrompt` workspace, passes only the SDK `evaluate`/`optimize` configuration to `AgentOptimizer.optimize`, and always uses `update_source=False`. Source write-back is disabled by default; it is considered only after a Gate-approved winner and guarded by prompt-digest checks.

## Outputs

Each run writes `optimization_report.json`, `optimization_report.md`, and secret-free audit evidence:

- `input.snapshot.json` and `environment.snapshot.json`
- `audit/raw_reports.json` and `audit/normalized_reports.json`
- `audit/candidate_reports.json` and `audit/gate_decisions.json`

The JSON report is Pydantic-readable as `OptimizationReport` and is the source of truth for the selected candidate.
