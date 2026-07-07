# Evaluation + Optimization Report

- Decision: **REJECT**
- Baseline train score: `0.3704`
- Candidate train score: `0.6667`
- Train delta: `+0.2963`
- Baseline validation score: `0.8148`
- Candidate validation score: `0.4815`
- Validation delta: `-0.3333`
- New passes: `val_format_json`
- New failures: `val_critical_discount, val_stable_refund`

## Gate Reasons

- validation score delta -0.3333 is below required +0.0500
- candidate introduced new hard failures: val_critical_discount, val_stable_refund
- critical cases regressed: val_critical_discount

## Train Case Delta

| case | baseline | candidate | delta | classification |
| --- | ---: | ---: | ---: | --- |
| `train_format_json` | 0.4444 | 1.0000 | +0.5556 | new_pass |
| `train_knowledge_gap` | 0.0000 | 0.0000 | +0.0000 | unchanged |
| `train_tool_args` | 0.6667 | 1.0000 | +0.3333 | new_pass |

## Validation Case Delta

| case | baseline | candidate | delta | classification |
| --- | ---: | ---: | ---: | --- |
| `val_critical_discount` | 1.0000 | 0.2222 | -0.7778 | new_failure |
| `val_format_json` | 0.4444 | 1.0000 | +0.5556 | new_pass |
| `val_stable_refund` | 1.0000 | 0.2222 | -0.7778 | new_failure |

## Failure Attribution

- `baseline_train`: format_noncompliance=2, knowledge_recall_insufficient=1, llm_rubric_not_met=2, tool_argument_error=1, tool_call_error=1
- `baseline_validation`: format_noncompliance=1, llm_rubric_not_met=1
- `candidate_train`: format_noncompliance=1, knowledge_recall_insufficient=1, llm_rubric_not_met=1, tool_call_error=1
- `candidate_validation`: final_response_mismatch=2, llm_rubric_not_met=2, tool_argument_error=2

## Audit

- Seed: `91`
- Backend: `fake`
- Duration seconds: `0.0065`
- Total fake/model calls: `12`
- Total cost: `0.0000`
