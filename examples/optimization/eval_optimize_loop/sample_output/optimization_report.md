# Optimization Report: REJECT

- Run ID: `sample-output-v1`
- Mode: `fake`
- Final stage: `complete`
- Duration: `0.390s`
- Reproducible: `yes`
- Source prompt applied: `no`

## Regression

| Split | Baseline score | Candidate score | Baseline pass rate | Candidate pass rate |
|---|---:|---:|---:|---:|
| train | 0.3333 | 1.0000 | 0.3333 | 1.0000 |
| validation | 0.6667 | 0.6667 | 0.6667 | 0.6667 |

## Validation Transitions

- `validation_improve`: `NEW_PASS` (+1.0000)
- `validation_regress`: `NEW_FAIL` (-1.0000)
- `validation_stable`: `UNCHANGED` (+0.0000)

## Gate

- Decision: `REJECT`
- Failed checks: `VALIDATION_SCORE_DELTA, NO_NEW_HARD_FAIL`
- Reasons: `VALIDATION_SCORE_DELTA_BELOW_MINIMUM, NEW_HARD_FAIL_BUDGET_EXCEEDED`
- Overfit detected: `no`

## Cost

- Total: `$0.000000`
