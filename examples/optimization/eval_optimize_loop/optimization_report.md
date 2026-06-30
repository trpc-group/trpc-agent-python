# Optimization Report

## Summary

Decision: REJECT. Train mean changed 0.25 -> 0.7833 (+0.5333); validation mean changed 0.7333 -> 0.6667 (-0.0666). New validation passes: ['val_warranty_new_pass']. New validation failures: ['val_smalltalk_regression', 'val_order_soft_degradation']. Gate reason: validation_gain_threshold; no_new_hard_fail; no_critical_regression; not_overfit_train_up_val_down.

## Scores

- Mode: `fake`
- Baseline train mean: 0.25
- Candidate train mean: 0.7833
- Baseline validation mean: 0.7333
- Candidate validation mean: 0.6667
- Decision: **REJECT**
- Reason: validation_gain_threshold; no_new_hard_fail; no_critical_regression; not_overfit_train_up_val_down

## Failure Attribution

- final_response_mismatch: 4
- format_error: 1
- knowledge_recall_insufficient: 2
- tool_call_error: 1

## Validation Delta

- `val_warranty_new_pass`: new_pass (0.2 -> 1.0, +0.8000)
- `val_smalltalk_regression`: new_fail (1.0 -> 0.45, -0.5500)
- `val_order_soft_degradation`: new_fail (1.0 -> 0.55, -0.4500)

## Gate Checks

- FAIL `validation_gain_threshold`: val_gain=-0.0666, required>=+0.1000
- FAIL `no_new_hard_fail`: new_hard_fails=['val_smalltalk_regression', 'val_order_soft_degradation']
- FAIL `no_critical_regression`: critical_regressions=['val_smalltalk_regression', 'val_order_soft_degradation']
- FAIL `not_overfit_train_up_val_down`: train_gain=+0.5333, val_gain=-0.0666
- PASS `cost_budget`: cost_usd=0.000000, budget=0.050000

## Audit

- Cost USD: 0.0
- Tokens: 0
- Duration seconds: 0.0176
- Baseline SHA-256: `30b490452eeb916fd25950797f0cbe1f9bac2a7b9f738775365c066b43924b88`
- Candidate SHA-256: `5d3271e9ab855a1bdf0d6af54e6f8521d35a4bd5727e89d632486f826f5f52b9`
