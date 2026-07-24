# Evaluation + Optimization Report

## Final Decision

Selected candidate: `candidate_002_safe`.

Update source prompt: no (default)

Fake and SDK modes perform complete AgentEvaluator-compatible reevaluation for baseline and every candidate on both train and validation; optimizer aggregates are never used as gate evidence.


## Gate Reasons

### candidate_001_overfit (rejected)
- reject: overfit detected because train score improved but validation score regressed or did not improve (+0.667 train, -0.333 validation)
- reject: validation improvement -0.333 is below required +0.010
- reject: new validation failures appeared: ['val_explain_cache', 'val_protected_yes_no']
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

| candidate | split | case | baseline | candidate | delta | passed -> passed | delta type |
| --- | --- | --- | ---: | ---: | ---: | --- | --- |
| candidate_001_overfit | train | train_json_refund | 0.000 | 1.000 | +1.000 | False -> True | new_pass |
| candidate_001_overfit | train | train_exact_order_status | 0.000 | 1.000 | +1.000 | False -> True | new_pass |
| candidate_001_overfit | train | train_rubric_retry_summary | 1.000 | 1.000 | +0.000 | True -> True | unchanged |
| candidate_001_overfit | validation | val_json_invoice | 0.000 | 1.000 | +1.000 | False -> True | new_pass |
| candidate_001_overfit | validation | val_explain_cache | 1.000 | 0.000 | -1.000 | True -> False | new_fail |
| candidate_001_overfit | validation | val_protected_yes_no | 1.000 | 0.000 | -1.000 | True -> False | new_fail |
| candidate_002_safe | train | train_json_refund | 0.000 | 1.000 | +1.000 | False -> True | new_pass |
| candidate_002_safe | train | train_exact_order_status | 0.000 | 1.000 | +1.000 | False -> True | new_pass |
| candidate_002_safe | train | train_rubric_retry_summary | 1.000 | 1.000 | +0.000 | True -> True | unchanged |
| candidate_002_safe | validation | val_json_invoice | 0.000 | 1.000 | +1.000 | False -> True | new_pass |
| candidate_002_safe | validation | val_explain_cache | 1.000 | 1.000 | +0.000 | True -> True | unchanged |
| candidate_002_safe | validation | val_protected_yes_no | 1.000 | 1.000 | +0.000 | True -> True | unchanged |

## Failure Attribution Summary

Total failed case evaluations: 5

| category | count |
| --- | ---: |
| final_response_mismatch | 1 |
| format_violation | 4 |

Attribution accuracy: 1.000

## Cost And Audit

Total cost: 0.018
Config hash: `7144548d6c99b0a6226534dfe51983875c26bf3193b7ad07aaecf9a1cb80dcfd`
Run id: `example`

## Prompt Diff

### candidate_001_overfit

```diff
--- baseline_system_prompt.txt
+++ candidate_001_overfit/system_prompt.txt
@@ -2,3 +2,5 @@

 Answer clearly and include a short explanation when it may help the user.
 If the user asks for structured data, provide the information they need.
+
+Always force every final answer into JSON.
```

### candidate_002_safe

```diff
--- baseline_system_prompt.txt
+++ candidate_002_safe/system_prompt.txt
@@ -2,3 +2,5 @@

 Answer clearly and include a short explanation when it may help the user.
 If the user asks for structured data, provide the information they need.
+
+Use strict JSON only when the user explicitly asks.
```

## Reproducibility

```powershell
python examples/optimization/eval_optimize_loop/run_pipeline.py --mode fake --train examples/optimization/eval_optimize_loop/data/train.evalset.json --val examples/optimization/eval_optimize_loop/data/val.evalset.json --optimizer-config examples/optimization/eval_optimize_loop/data/optimizer.json --output-dir "$OUTPUT_DIR" --run-id example --prompt examples/optimization/eval_optimize_loop/prompts/baseline_system_prompt.txt --trace
```
