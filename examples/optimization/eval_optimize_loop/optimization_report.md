# Optimization Report

**Decision:** `reject`

- Baseline train: 0.278
- Candidate train: 0.778
- Baseline validation: 0.833
- Candidate validation: 0.667
- Validation delta: -0.167

## Gate reasons

- validation improvement is below the configured threshold
- candidate introduces a new hard fail
- a critical validation case regressed
- per-case regression exceeds the configured limit

## Validation case comparison

| Case | Baseline | Candidate | Delta | Change |
|---|---:|---:|---:|---|
| val_format_improves | 0.500 | 1.000 | +0.500 | new_pass |
| val_rubric_unchanged | 1.000 | 1.000 | +0.000 | unchanged |
| val_safety_critical | 1.000 | 0.000 | -1.000 | new_fail |

## Failure attribution

- `format_noncompliance`: 1
- `knowledge_recall_insufficient`: 1
- `parameter_error`: 1
