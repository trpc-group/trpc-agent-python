# Evaluation + Optimization Report

## Final Decision

Selected candidate: `candidate_002_safe`.

## Gate Reasons

### candidate_001_overfit (rejected)
- reject: train score improved but validation score regressed (+0.667 train, -0.333 validation)
- reject: validation improvement -0.333 is below required +0.010
- reject: new hard failures appeared: ['val_explain_cache', 'val_protected_yes_no']
- reject: protected cases regressed: ['val_protected_yes_no']
- reject: per-case validation score drops exceed 0.000: ['val_explain_cache', 'val_protected_yes_no']

### candidate_002_safe (accepted)
- accept: validation score improved +0.333 with no protected regression or new hard failure

## Baseline vs Candidate Scores

| prompt | train score | validation score | gate |
| --- | ---: | ---: | --- |
| baseline | 0.333 | 0.667 | n/a |
| candidate_001_overfit | 1.000 | 0.333 | reject |
| candidate_002_safe | 1.000 | 1.000 | accept |

## Per-Case Delta

| candidate | split | case | baseline | candidate | delta | passed -> passed |
| --- | --- | --- | ---: | ---: | ---: | --- |
| candidate_001_overfit | train | train_json_refund | 0.000 | 1.000 | +1.000 | False -> True |
| candidate_001_overfit | train | train_exact_order_status | 0.000 | 1.000 | +1.000 | False -> True |
| candidate_001_overfit | train | train_rubric_retry_summary | 1.000 | 1.000 | +0.000 | True -> True |
| candidate_001_overfit | validation | val_json_invoice | 0.000 | 1.000 | +1.000 | False -> True |
| candidate_001_overfit | validation | val_explain_cache | 1.000 | 0.000 | -1.000 | True -> False |
| candidate_001_overfit | validation | val_protected_yes_no | 1.000 | 0.000 | -1.000 | True -> False |
| candidate_002_safe | train | train_json_refund | 0.000 | 1.000 | +1.000 | False -> True |
| candidate_002_safe | train | train_exact_order_status | 0.000 | 1.000 | +1.000 | False -> True |
| candidate_002_safe | train | train_rubric_retry_summary | 1.000 | 1.000 | +0.000 | True -> True |
| candidate_002_safe | validation | val_json_invoice | 0.000 | 1.000 | +1.000 | False -> True |
| candidate_002_safe | validation | val_explain_cache | 1.000 | 1.000 | +0.000 | True -> True |
| candidate_002_safe | validation | val_protected_yes_no | 1.000 | 1.000 | +0.000 | True -> True |

## Failure Attribution Summary

Total failed case evaluations: 5

| category | count |
| --- | ---: |
| final_answer_mismatch | 2 |
| format_violation | 3 |

## Prompt Diff

### candidate_001_overfit

```diff
+ OPTIMIZER_MARKER: ALWAYS_OUTPUT_JSON
+ Always force every final answer into JSON, even when the user asks for prose.
```

### candidate_002_safe

```diff
+ OPTIMIZER_MARKER: STRICT_WHEN_REQUESTED
+ Use strict JSON only when explicitly requested.
+ Preserve natural-language answers unless a strict format is requested.
```

## Reproducibility

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py --train examples/optimization/eval_optimize_loop/data/train.evalset.json --val examples/optimization/eval_optimize_loop/data/val.evalset.json --optimizer-config examples/optimization/eval_optimize_loop/data/optimizer.json --prompt examples/optimization/eval_optimize_loop/prompts/baseline_system_prompt.txt --output-dir /tmp/eval-optimize-loop --fake-model --fake-judge --trace
```
