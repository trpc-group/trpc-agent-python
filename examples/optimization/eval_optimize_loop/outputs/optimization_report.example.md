# Evaluation + Optimization Report

## Final Decision

Selected candidate: `candidate_002_safe`.

Update source prompt: no (default)


## Gate Reasons

### candidate_001_overfit (rejected)
- reject: overfit detected because train score improved but validation score regressed or did not improve (+0.667 train, -0.333 validation)
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
| final_response_mismatch | 2 |
| format_violation | 3 |

Attribution accuracy: 1.000

## Cost And Audit

Total cost: 0.018
Config hash: `66a1db3a1c84ad12fd41385fc5e1a9c23cc305e6973945c4bade559c804f9abe`
Run id: `eval_optimize_loop_seed_91`

## Prompt Diff

### candidate_001_overfit

```diff
--- baseline_system_prompt.txt
+++ candidate_001_overfit/system_prompt.txt
@@ -2,3 +2,7 @@

 Answer clearly and include a short explanation when it may help the user.
 If the user asks for structured data, provide the information they need.
+
+# Optimizer patch
+OPTIMIZER_MARKER: ALWAYS_OUTPUT_JSON
+Always force every final answer into JSON, even when the user asks for prose.
```

### candidate_002_safe

```diff
--- baseline_system_prompt.txt
+++ candidate_002_safe/system_prompt.txt
@@ -2,3 +2,9 @@

 Answer clearly and include a short explanation when it may help the user.
 If the user asks for structured data, provide the information they need.
+
+# Optimizer patch
+OPTIMIZER_MARKER: STRICT_WHEN_REQUESTED
+Use strict JSON only when the user explicitly asks for JSON.
+Use exact answers only when the user explicitly asks for an exact answer.
+Otherwise answer naturally and honor rubric constraints.
```

## Reproducibility

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py --train examples/optimization/eval_optimize_loop/data/train.evalset.json --val examples/optimization/eval_optimize_loop/data/val.evalset.json --optimizer-config examples/optimization/eval_optimize_loop/data/optimizer.json --prompt examples/optimization/eval_optimize_loop/prompts/baseline_system_prompt.txt --output-dir /tmp/eval-optimize-loop --fake-model --fake-judge --trace
```
