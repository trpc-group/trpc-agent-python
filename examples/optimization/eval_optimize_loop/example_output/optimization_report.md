# Optimization Report

## Verdict

**✗ REJECT**

- newly failing in val not allowed: ['case_val_regression']
- min_improvement met: val pass_rate delta 0.0000 >= 0.0000
- duration check passed: 0.01s <= 180s

## Pass Rates

| Split | Baseline | Candidate | Delta |
|---|---|---|---|
| train | 0.3333 | 0.3333 | +0.0000 |
| val | 0.3333 | 0.3333 | +0.0000 |

### Metric Breakdown (val)

| Metric | Baseline | Candidate | Delta |
|---|---|---|---|
| final_response_avg_score | 0.3333 | 0.3333 | +0.0000 |

## Per-Case Delta

### train set

| Case ID | Baseline | Candidate | Status |
|---|---|---|---|
| case_train_always_fail | FAIL | FAIL | failed (both) |
| case_train_optimizable | FAIL | PASS | newly passing |
| case_train_regression | PASS | FAIL | newly failing |

### val set

| Case ID | Baseline | Candidate | Status |
|---|---|---|---|
| case_val_improves | FAIL | PASS | newly passing |
| case_val_no_change | FAIL | FAIL | failed (both) |
| case_val_regression | PASS | FAIL | newly failing |

## Failure Attribution

- Total cases: 3
- Failed (baseline train): 2

| Category | Count | Case IDs |
|---|---|---|
| final_response_mismatch | 2 | case_train_optimizable, case_train_always_fail |
| format_violation | 1 | case_train_always_fail |

## Gate Results

- newly failing in val not allowed: ['case_val_regression']
- min_improvement met: val pass_rate delta 0.0000 >= 0.0000
- duration check passed: 0.01s <= 180s

## Overfitting Check

No overfitting detected.

## Audit

| Field | Value |
|---|---|
| Mode | trace |
| Duration | 0.01s |
| Cost | $0.0000 |
| Seed | 42 |
| Started | 2026-07-16T03:39:56.304373+00:00 |
| Finished | 2026-07-16T03:39:56.315185+00:00 |
| Schema version | v1 |

