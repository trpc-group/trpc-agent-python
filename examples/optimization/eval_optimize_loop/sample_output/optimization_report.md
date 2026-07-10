# Optimization Report

- Mode: `fake`
- Seed: `42`
- Selected candidate: `candidate_general_fix`
- Source integrity: `restored`

## Baseline

| Split | Pass rate | Aggregate score |
| --- | ---: | ---: |
| train | 0.000 | 0.000 |
| validation | 0.667 | 0.667 |

## Candidates

| Candidate | Decision | Train score | Validation score |
| --- | --- | ---: | ---: |
| `candidate_general_fix` | ACCEPT | 0.667 | 1.000 |
| `candidate_noop` | REJECT | 0.000 | 0.667 |
| `candidate_overfit` | REJECT | 1.000 | 0.667 |

## Validation deltas

Each candidate section below contains its complete validation case delta table.

## Gate rules

Each candidate section below contains its complete gate rule table.

### `candidate_general_fix`

Decision: **ACCEPT**
Reasons: candidate passed all independent gate rules

#### Validation deltas

| Case | Transition | Score delta | Critical |
| --- | --- | ---: | --- |
| `val_stable_faq` | UNCHANGED | +0.000 | no |
| `val_json_generalization` | NEW_PASS | +1.000 | no |
| `val_refund_critical` | UNCHANGED | +0.000 | yes |

#### Gate rules

| Rule | Passed | Actual | Expected |
| --- | --- | ---: | ---: |
| `validation_score_improved` | yes | `0.33333333333333337` | `0.05` |
| `validation_pass_rate_not_worse` | yes | `0.33333333333333337` | `0.0` |
| `new_hard_fails` | yes | `0` | `0` |
| `validation_regressions` | yes | `0` | `0` |
| `no_critical_regression` | yes | `False` | `False` |
| `no_overfit` | yes | `False` | `False` |

### `candidate_noop`

Decision: **REJECT**
Reasons: validation aggregate score must improve

#### Validation deltas

| Case | Transition | Score delta | Critical |
| --- | --- | ---: | --- |
| `val_stable_faq` | UNCHANGED | +0.000 | no |
| `val_json_generalization` | UNCHANGED | +0.000 | no |
| `val_refund_critical` | UNCHANGED | +0.000 | yes |

#### Gate rules

| Rule | Passed | Actual | Expected |
| --- | --- | ---: | ---: |
| `validation_score_improved` | no | `0.0` | `0.05` |
| `validation_pass_rate_not_worse` | yes | `0.0` | `0.0` |
| `new_hard_fails` | yes | `0` | `0` |
| `validation_regressions` | yes | `0` | `0` |
| `no_critical_regression` | yes | `False` | `False` |
| `no_overfit` | yes | `False` | `False` |

### `candidate_overfit`

Decision: **REJECT**
Reasons: validation aggregate score must improve; new hard failures are not allowed; validation regressions exceed the limit; critical validation cases must not regress

#### Validation deltas

| Case | Transition | Score delta | Critical |
| --- | --- | ---: | --- |
| `val_stable_faq` | UNCHANGED | +0.000 | no |
| `val_json_generalization` | NEW_PASS | +1.000 | no |
| `val_refund_critical` | REGRESSION | -1.000 | yes |

#### Gate rules

| Rule | Passed | Actual | Expected |
| --- | --- | ---: | ---: |
| `validation_score_improved` | no | `0.0` | `0.05` |
| `validation_pass_rate_not_worse` | yes | `0.0` | `0.0` |
| `new_hard_fails` | no | `1` | `0` |
| `validation_regressions` | no | `1` | `0` |
| `no_critical_regression` | no | `True` | `False` |
| `no_overfit` | yes | `False` | `False` |
## Reproduction

```text
python examples/optimization/eval_optimize_loop/run_pipeline.py --mode fake --output-dir <output-dir>
```
