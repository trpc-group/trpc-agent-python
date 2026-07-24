# Evaluation + Optimization Regression Report

- **Decision:** `accepted`
- **Selected candidate:** `robust`
- **Optimizer:** `gepa_reflective` / `SUCCEEDED`
- **Seed:** `91`

## Baseline

| Split | Pass rate | Average score | Cases |
| --- | ---: | ---: | ---: |
| Train | 0.000 | 0.667 | 3 |
| Validation | 0.333 | 0.833 | 3 |

## Candidate matrix

| Candidate | Train pass | Validation pass | Validation delta | Paired CI | Gate | Overfit | Pareto |
| --- | ---: | ---: | ---: | --- | --- | --- | --- |
| `ineffective` | 0.000 | 0.333 | +0.000 | [+0.000, +0.000] | FAIL | False | False |
| `overfit` | 1.000 | 0.000 | -0.333 | [-1.000, +0.000] | FAIL | True | False |
| `robust` | 1.000 | 1.000 | +0.667 | [+0.000, +1.000] | PASS | False | True |

## Candidate

Candidate `robust` was independently re-evaluated.

| Split | Baseline | Candidate | Delta |
| --- | ---: | ---: | ---: |
| Train pass rate | 0.000 | 1.000 | +1.000 |
| Validation pass rate | 0.333 | 1.000 | +0.667 |

Paired bootstrap interval: `[+0.000, +1.000]` at 95% confidence.

### Per-case delta

| Case | Split | Status | Score delta |
| --- | --- | --- | ---: |
| `train_final_response_mismatch` | train | newly_passed | +0.250 |
| `train_parameter_error` | train | newly_passed | +0.500 |
| `train_tool_call_error` | train | newly_passed | +0.250 |
| `val_format_key_case` | validation | unchanged | +0.000 |
| `val_knowledge_recall_insufficiency` | validation | newly_passed | +0.375 |
| `val_llm_rubric_failure` | validation | newly_passed | +0.125 |

## Gate

| Candidate | Check | Required | Result | Actual | Expected |
| --- | --- | --- | --- | --- | --- |
| `ineffective` | `optimizer_succeeded` | True | PASS | `SUCCEEDED` | `SUCCEEDED` |
| `ineffective` | `minimum_validation_gain` | True | FAIL | `0.0` | `>= 0.5` |
| `ineffective` | `no_new_hard_failures` | True | PASS | `[]` | `[]` |
| `ineffective` | `key_cases_no_regression` | True | PASS | `[]` | `[]` |
| `ineffective` | `no_train_validation_overfit` | True | PASS | `False` | `False` |
| `ineffective` | `validation_gain_ci_lower_bound` | True | PASS | `0.0` | `>= 0.0` |
| `ineffective` | `metric_call_budget` | True | PASS | `24` | `<= 80` |
| `ineffective` | `token_budget` | True | PASS | `368` | `<= 200000` |
| `ineffective` | `duration_budget` | True | PASS | `0.009248999878764153` | `<= 180` |
| `ineffective` | `cost_budget` | True | PASS | `0.0` | `<= 0.0` |
| `overfit` | `optimizer_succeeded` | True | PASS | `SUCCEEDED` | `SUCCEEDED` |
| `overfit` | `minimum_validation_gain` | True | FAIL | `-0.3333333333333333` | `>= 0.5` |
| `overfit` | `no_new_hard_failures` | True | FAIL | `['val_format_key_case']` | `[]` |
| `overfit` | `key_cases_no_regression` | True | FAIL | `['val_format_key_case']` | `[]` |
| `overfit` | `no_train_validation_overfit` | True | FAIL | `True` | `False` |
| `overfit` | `validation_gain_ci_lower_bound` | True | FAIL | `-1.0` | `>= 0.0` |
| `overfit` | `metric_call_budget` | True | PASS | `24` | `<= 80` |
| `overfit` | `token_budget` | True | PASS | `392` | `<= 200000` |
| `overfit` | `duration_budget` | True | PASS | `0.010194707894697785` | `<= 180` |
| `overfit` | `cost_budget` | True | PASS | `0.0` | `<= 0.0` |
| `robust` | `optimizer_succeeded` | True | PASS | `SUCCEEDED` | `SUCCEEDED` |
| `robust` | `minimum_validation_gain` | True | PASS | `0.6666666666666667` | `>= 0.5` |
| `robust` | `no_new_hard_failures` | True | PASS | `[]` | `[]` |
| `robust` | `key_cases_no_regression` | True | PASS | `[]` | `[]` |
| `robust` | `no_train_validation_overfit` | True | PASS | `False` | `False` |
| `robust` | `validation_gain_ci_lower_bound` | True | PASS | `0.0` | `>= 0.0` |
| `robust` | `metric_call_budget` | True | PASS | `24` | `<= 80` |
| `robust` | `token_budget` | True | PASS | `444` | `<= 200000` |
| `robust` | `duration_budget` | True | PASS | `0.010438166093081236` | `<= 180` |
| `robust` | `cost_budget` | True | PASS | `0.0` | `<= 0.0` |

## Failure attribution

Explained 13/13 failed cases (100.0%).

| Case | Category | Explanation |
| --- | --- | --- |
| `baseline/train/train_final_response_mismatch` | `final_response_mismatch` | The final response does not contain the expected reference answer. |
| `baseline/train/train_tool_call_error` | `tool_call_error` | A required tool returned an execution error. |
| `baseline/train/train_tool_call_error` | `final_response_mismatch` | The final response does not contain the expected reference answer. |
| `baseline/train/train_parameter_error` | `parameter_error` | A tool was selected correctly but received different arguments. |
| `baseline/train/train_parameter_error` | `final_response_mismatch` | The final response does not contain the expected reference answer. |
| `baseline/validation/val_llm_rubric_failure` | `llm_rubric_failure` | The final answer failed one or more non-format quality rubrics. |
| `baseline/validation/val_knowledge_recall_insufficiency` | `knowledge_recall_insufficiency` | Retrieved knowledge did not cover the facts required by the rubric. |
| `baseline/validation/val_knowledge_recall_insufficiency` | `llm_rubric_failure` | The final answer failed one or more non-format quality rubrics. |
| `candidate/ineffective/train/train_final_response_mismatch` | `final_response_mismatch` | The final response does not contain the expected reference answer. |
| `candidate/ineffective/train/train_tool_call_error` | `tool_call_error` | A required tool returned an execution error. |
| `candidate/ineffective/train/train_tool_call_error` | `final_response_mismatch` | The final response does not contain the expected reference answer. |
| `candidate/ineffective/train/train_parameter_error` | `parameter_error` | A tool was selected correctly but received different arguments. |
| `candidate/ineffective/train/train_parameter_error` | `final_response_mismatch` | The final response does not contain the expected reference answer. |
| `candidate/ineffective/validation/val_llm_rubric_failure` | `llm_rubric_failure` | The final answer failed one or more non-format quality rubrics. |
| `candidate/ineffective/validation/val_knowledge_recall_insufficiency` | `knowledge_recall_insufficiency` | Retrieved knowledge did not cover the facts required by the rubric. |
| `candidate/ineffective/validation/val_knowledge_recall_insufficiency` | `llm_rubric_failure` | The final answer failed one or more non-format quality rubrics. |
| `candidate/overfit/validation/val_llm_rubric_failure` | `llm_rubric_failure` | The final answer failed one or more non-format quality rubrics. |
| `candidate/overfit/validation/val_knowledge_recall_insufficiency` | `knowledge_recall_insufficiency` | Retrieved knowledge did not cover the facts required by the rubric. |
| `candidate/overfit/validation/val_knowledge_recall_insufficiency` | `llm_rubric_failure` | The final answer failed one or more non-format quality rubrics. |
| `candidate/overfit/validation/val_format_key_case` | `format_failure` | The format rubric failed for the final response. |
| `candidate/overfit/validation/val_format_key_case` | `final_response_mismatch` | The final response does not contain the expected reference answer. |

## Audit

- Run id: `offline-87974874280c`
- Duration: `0.099s`
- Metric calls: `27`
- Tokens: `7179`
- Cost measurement: `measured_zero_offline`
- Optimizer artifacts: `optimizer`

### Candidate evaluation resources

| Candidate | Metric calls | Judge calls | Tokens | P95 latency | Duration | Cost | Cost measurement |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `ineffective` | 24 | 12 | 368 | 8.0 ms | 0.009 s | $0.0000 | `measured_zero_offline` |
| `overfit` | 24 | 12 | 392 | 8.0 ms | 0.010 s | $0.0000 | `measured_zero_offline` |
| `robust` | 24 | 12 | 444 | 8.0 ms | 0.010 s | $0.0000 | `measured_zero_offline` |
