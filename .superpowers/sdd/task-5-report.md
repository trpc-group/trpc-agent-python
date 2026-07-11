# Task 5 Report: Enforced Optimization Report Contract

## Scope

- Tightened `optimization_report.schema.json` around evaluation cases, deltas,
  candidate audit data, gates, attribution, costs, and optimizer rounds.
- Added a recursive finite-number guard to `validate_report_schema`.
- Regenerated the fake sample report from the current pipeline, then normalized
  paths, environment fields, run id, and every `duration_seconds` value.

## RED Evidence

Command:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py -k "report_schema_rejects_incomplete_core_objects or report_schema_rejects_nonfinite_numbers or report_schema_requires_numeric_delta_for_accepted_gate or report_schema_requires_consistent_candidate_audit_cost or report_schema_allows_empty_no_run_case_metrics" -q
```

Before the contract change, this produced 10 failures: incomplete core objects,
out-of-range coverage, non-finite numbers, accepted gates with null deltas, and
inconsistent audit costs were accepted. The stale pre-Task-4 sample also lacked
`candidate.audit`, which caused the audit-path mutations to raise `KeyError`.

## GREEN Evidence

Focused schema mutations:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py -k "report_schema" -q
```

Result: `18 passed`.

Full example suite:

```powershell
python -m pytest -o addopts="" tests/evaluation/test_eval_optimize_loop_example.py -q
```

Result: `104 passed, 1 skipped, 1 warning`. The skip is the existing opt-in
online smoke test. The warning is the existing LangGraph deprecation warning.

Pipeline reports were written and then validated through
`validate_report_schema` for both modes:

```powershell
python examples/optimization/eval_optimize_loop/run_pipeline.py --mode fake --output-dir "$env:TEMP\trpc-task5-schema-verify-fake" --run-id schema_fake
python examples/optimization/eval_optimize_loop/run_pipeline.py --mode trace --output-dir "$env:TEMP\trpc-task5-schema-verify-trace" --run-id schema_trace
```

Both reports validated successfully. The regenerated fixture was additionally
checked to contain no worktree or temporary-source paths, to use `run_id` equal
to `sample`, to retain `optimization_rounds: []`, and to set all transient
`duration_seconds` values to `0.0`.

## Contract Decisions

- `evaluationCase.metrics` remains required and object-typed, but has no
  `minProperties`, preserving AgentEvaluator's legitimate no-run `metrics: {}`
  branch.
- `candidate.audit` and top-level `optimization_rounds` are required.
- Candidate gates require a numeric `validation_delta` when accepted. Rejected
  gates may use null for malformed evidence.
- Candidate audit costs enforce `known: true` with a numeric estimate and
  `known: false` with a null estimate.
- The validator rejects `NaN`, positive infinity, and negative infinity before
  JSON Schema validation or report writing.
