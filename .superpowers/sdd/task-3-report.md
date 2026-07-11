# Task 3 Report: Rule-first attribution and offline trace mode

## Scope

- Added deterministic `attribute_case()` classification in the example pipeline.
- Added an offline four-case trace EvalSet and trace-only CLI mode.
- Extended only example contracts and reporting to persist attribution and trace artifacts.
- No SDK public API was edited. Trace mode uses no credentials, agent inference, optimizer, network judge, or model object.

## TDD evidence

### RED

Command:

```text
python -m pytest tests/examples/optimization/eval_optimize_loop/test_attribution_and_trace.py -q
```

Result before implementation: `15 failed` (exit code 1). The failures were the intended missing-feature signals: `pipeline.attribution` did not exist, `run_trace_pipeline` was not exported, and the CLI rejected `--mode trace`. A subsequent added boundary test also failed as expected before the implementation treated every recorded non-timeout execution error as `EXECUTION_ERROR`.

### GREEN

The focused Task 3 suite passed after implementation:

```text
16 passed
```

It covers all rule precedence categories, judge fallback/invalid output, structural-rule precedence over judge output, trace evaluation without `TRPC_AGENT_API_KEY`, generated report/artifact files, and the trace CLI.

## Final verification

```text
python -m pytest tests/examples/optimization/eval_optimize_loop/test_attribution_and_trace.py -q
# 16 passed

python -m pytest tests/examples/optimization/eval_optimize_loop -q
# 49 passed

python examples/optimization/eval_optimize_loop/run_pipeline.py --mode trace --output-dir <temp-dir>
# exit 0; wrote optimization_report.json, optimization_report.md,
# trace_raw_results.json, and trace_normalized_cases.json
```

`git diff --check` completed without whitespace errors.

## Notes

- The invalid-format trace uses a reference with `tool: none`, so no higher-priority tool-selection mismatch exists; it therefore correctly reaches `FORMAT_VIOLATION` under the required precedence.
- Test and CLI runs emit pre-existing third-party dependency warnings from `requests` and `langgraph`; they do not affect exit status.
