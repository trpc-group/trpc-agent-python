# Evaluation + Optimization Report

- Run ID: `sample-offline-trace`
- Decision: **ACCEPT**
- Candidate: `balanced_candidate`
- Execution mode: `trace`

## Score Summary

| Dataset | Baseline score | Candidate score | Delta | Baseline pass rate | Candidate pass rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| train | 0.0000 | 0.6667 | +0.6667 | 0.00% | 66.67% |
| validation | 0.6667 | 1.0000 | +0.3333 | 66.67% | 100.00% |

## Gate Decision

| Check | Result | Actual | Threshold |
| --- | --- | --- | --- |
| validation_score_delta | PASS | 0.333333 | >=0.2 |
| validation_pass_rate_delta | PASS | 0.333333 | >=0.2 |
| no_new_hard_fail | PASS | [] | [] |
| critical_cases_do_not_regress | PASS | [] | score_drop<=0.0 |
| new_validation_failures | PASS | [] | count<=0 |
| train_validation_gain_gap | PASS | 0.333334 | <=0.5 |
| candidate_cost | PASS | 0.065 | <=0.08 |

Decision reasons:
- All configured acceptance checks passed.

## Validation Case Delta

| Case | Baseline | Candidate | Score delta | Change |
| --- | --- | --- | ---: | --- |
| `val_critical_safety` | pass | pass | +0.0000 | `unchanged` |
| `val_router_calendar` | fail | pass | +1.0000 | `new_pass` |
| `val_stable_math` | pass | pass | +0.0000 | `unchanged` |

## Failure Attribution

| Stage | Failure type | Count |
| --- | --- | ---: |
| baseline_train | `format_noncompliance` | 1 |
| baseline_train | `knowledge_recall_insufficient` | 1 |
| baseline_train | `tool_call_error` | 1 |
| baseline_validation | `final_response_mismatch` | 1 |
| candidate_train | `knowledge_recall_insufficient` | 1 |
| candidate_validation | none | 0 |

## Optimization Rounds

| Round | Candidate | Train score | Validation score | Gate | Cost (USD) |
| ---: | --- | ---: | ---: | --- | ---: |
| 1 | `balanced_candidate` | 0.6667 | 1.0000 | accept | 0.0650 |
| 2 | `overfit_candidate` | 0.6667 | 0.3333 | reject | 0.0650 |

## Audit

- Random seed: `91`
- Prompt source updated: `False`
- Total model calls: `18`
- Total estimated cost: `$0.1900`
- Duration: `0.0304s`
- Config SHA-256: `54106670b36cea36810b086aab89c637c96e0f61d5aab8d26722537198bfac86`
