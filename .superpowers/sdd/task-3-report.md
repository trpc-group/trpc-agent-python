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

## P1 reviewer follow-up

### Changes

- Tool-call snapshots now come from each evaluator invocation's `intermediate_data.tool_uses`, rather than the fake final-response JSON.
- `ToolCallSnapshot.arguments` preserves the raw value, including invalid non-dict shapes such as `[]`, so structural comparison can classify `[]` versus `{}` as `TOOL_ARGUMENT_ERROR`.
- `CaseSnapshot.tool_responses` now preserves `intermediate_data.tool_responses` values. Explicit `error`, `failed`, failed `status`, or empty responses yield rule-first `TOOL_EXECUTION_ERROR` before final-response metrics.
- The trace EvalSet now includes a final-JSON-match / intermediate-tool-mismatch case and an explicit tool-response-error case. It evaluates trajectories locally with `tool_trajectory_avg_score`.

### P1 RED/GREEN evidence

Before the follow-up implementation, the three new normalizer tests failed because final fake JSON overwrote intermediate calls, `[]` was coerced to `{}`, and `CaseSnapshot` lacked `tool_responses`. The trace E2E expectation also failed before the tool-response-error fixture existed.

After implementation:

```text
python -m pytest tests/examples/optimization/eval_optimize_loop/test_attribution_and_trace.py -q
# 19 passed

python -m pytest tests/examples/optimization/eval_optimize_loop -q
# 52 passed

python examples/optimization/eval_optimize_loop/run_pipeline.py --mode trace --output-dir <temp-dir>
# exit 0; normalized output preserves intermediate lookup_refund versus lookup_order,
# and tool_responses.error as raw structural evidence
```

## P2 reviewer follow-up

`normalize_eval_results()` now uses intermediate tool calls only when a recorded call exists. For black-box fake evaluation, where `RemoteEvalService` deliberately leaves actual `intermediate_data` empty, it falls back to the parsed fake JSON calls. The fake regression confirms the baseline actual `lookup_order` with `{}` arguments and reference `lookup_order` with `{"order_id": "A100"}` arguments both remain visible.

Verification after the P2 change:

```text
python -m pytest tests/examples/optimization/eval_optimize_loop/test_fake_loop.py -q -k keeps_parsed_json_tool_calls
# 1 passed

python -m pytest tests/examples/optimization/eval_optimize_loop/test_attribution_and_trace.py -q
# 19 passed

python -m pytest tests/examples/optimization/eval_optimize_loop -q
# 53 passed

python examples/optimization/eval_optimize_loop/run_pipeline.py --mode trace --output-dir <temp-dir>
# exit 0
```

## Final P1 reviewer follow-up

Fallback now depends on whether an invocation has `intermediate_data is None`, rather than whether extracting its `tool_uses` produced an empty list. An explicit `IntermediateData(tool_uses=[])` is therefore authoritative and remains empty even if the final fake JSON claims a tool. The matching regression verifies the missing-tool structural attribution against a reference trace that contains `lookup_order`.

Verification after this final adjustment:

```text
python -m pytest tests/examples/optimization/eval_optimize_loop/test_attribution_and_trace.py -q
# 20 passed

python -m pytest tests/examples/optimization/eval_optimize_loop -q
# 54 passed

python examples/optimization/eval_optimize_loop/run_pipeline.py --mode trace --output-dir <temp-dir>
# exit 0
```
