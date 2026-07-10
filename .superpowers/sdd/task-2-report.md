# Task 2 Report: Make Gate Decisions Total, Strict, and Fail-Closed

## Scope

Implemented Task 2 in the assigned worktree on branch `codex/eval-optimize-hardening`.

Changed only:

- `examples/optimization/eval_optimize_loop/run_pipeline.py`
- `tests/evaluation/test_eval_optimize_loop_example.py`
- `.superpowers/sdd/task-2-report.md`

Task 1's `metric_failed` attribution changes remain intact.

## TDD Record

### RED

Added `test_gate_fails_closed_for_boundary_and_invalid_evidence` covering:

- exact validation-delta boundary,
- missing candidate case,
- unexpected candidate case,
- non-finite candidate validation score,
- JSON serialization of the returned gate result.

Command:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py::test_gate_fails_closed_for_boundary_and_invalid_evidence -q
```

Result: `1 failed`.

The first failure showed that the existing gate accepted an exact `0.5` improvement when `min_validation_delta` was `0.5`. The remaining defects were then reachable in the same test: missing cases were not rejected, extra cases raised a `KeyError`, and `NaN` evidence was accepted.

### GREEN

Implemented the smallest gate hardening change:

- Added finite-number parsing with non-finite values represented as `None`.
- Added total case indexing that reports malformed entries and duplicate IDs instead of raising.
- Rejected missing and unexpected candidate case IDs before case-level comparisons.
- Restricted hard-fail and critical-regression checks to the shared case set.
- Required strict improvement: `validation_delta > min_validation_delta`.
- Rejected malformed case evidence, invalid metrics, and non-finite budget evidence.
- Returned `missing_case_ids`, `unexpected_case_ids`, and nullable `validation_delta`.
- Kept the gate result JSON serializable, including for invalid numeric evidence.

Focused adversarial test:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py::test_gate_fails_closed_for_boundary_and_invalid_evidence -q
```

Result: `1 passed`.

Gate regression and budget tests:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py -k "gate or regression or cost_budget" -q
```

Result: `7 passed`.

Full example test file:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py -q
```

Result: `33 passed, 1 skipped`.

The skip is the existing opt-in online smoke test. The run emitted an existing LangGraph/LangChain deprecation warning during import; it did not affect the result.

Additional check:

```powershell
git diff --check
```

Result: passed.

## High-Finding Follow-Up

### RED

Added `test_build_candidate_report_sanitizes_nonfinite_case_reasons` using a failed validation case with `reasons=[float("nan")]` and requiring strict JSON serialization.

Command:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py::test_build_candidate_report_sanitizes_nonfinite_case_reasons -q
```

Result: `1 failed` with `ValueError: Out of range float values are not JSON compliant`.

### GREEN

Wrapped the complete candidate report in `_json_safe` after constructing `case_deltas` and `failure_attribution`. This sanitizes raw nested reason values while preserving the gate result and its rejected decision.

Focused regression:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py::test_build_candidate_report_sanitizes_nonfinite_case_reasons -q
```

Result: `1 passed`.

Relevant malformed/gate suite:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py -k "malformed or gate or regression or cost_budget or case_set_mismatch or required_metric" -q
```

Result: `39 passed`.

Full example test file:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py -q
```

Result: `61 passed, 1 skipped`.

The skip remains the existing opt-in online smoke test; the existing LangGraph/LangChain deprecation warning remains non-blocking.

Final `git diff --check`: passed.

No report schema changes were made.

## Reopened Review Fix

The review identified four blocking defects in the first implementation:

- raw string/bool duration and cost values still reached comparisons and formatting;
- required metric status used truthiness instead of `is True`;
- `build_case_deltas` indexed only candidate cases and raised on mismatches;
- malformed case and numeric evidence could still raise or leak non-JSON floats into reports.

### RED

Added focused regression coverage for malformed numeric evidence, duplicate and malformed case sets, string metric status, union case deltas, mismatched candidate reports, and JSON-safe report output.

Command:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py -k "malformed_numeric or malformed_case_sets or required_metric_passed or build_candidate_report_rejects" -q
```

Result: `9 failed, 13 passed`.

The failures reproduced the reviewer findings: raw string duration/cost values raised `TypeError`, bool/string/config values were accepted, metric `"false"` was treated as passing, and mismatched case IDs raised `KeyError` in `build_case_deltas`.

### GREEN

The follow-up fix now:

- normalizes numeric evidence once and rejects strings, booleans, missing, and non-finite values;
- normalizes and validates numeric gate configuration before comparisons;
- requires required metric `passed` to be exactly `True`;
- builds deterministic union case deltas with `missing_candidate` and `unexpected_candidate` markers;
- makes attribution and candidate report construction total for malformed validation cases;
- sanitizes non-finite values before returning report data.

Focused regression suite:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py -k "malformed_numeric or malformed_case_sets or required_metric_passed or case_set_mismatch or malformed_validation_cases" -q
```

Result: `27 passed`.

Full example test file:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py -q
```

Result: `60 passed, 1 skipped`.

The skip remains the existing opt-in online smoke test. The run emitted the existing LangGraph/LangChain deprecation warning during import.

Final diff check:

```powershell
git diff --check
```

Result: passed.
