# Evaluation + Optimization Loop — Stage 3b

This example now provides the deterministic offline foundation for an auditable
evaluation and prompt-optimization loop. Stage 1 validates inputs and creates
an isolated prompt workspace. Stage 2 evaluates that baseline on train and
validation data, generates a fake prompt candidate, and evaluates the candidate
on both datasets again. Stage 3a normalizes the four SDK result sets, attributes
failures from available evidence, and builds train/validation case diffs. Stage
3b applies an independent Gate to those diffs and returns a complete acceptance
decision without writing the source prompt.

No model, API key, judge, or optimizer is used. The fake agent reads explicit
capability rules from the current working prompt, while the fake candidate
provider can generate an improving, behaviorally equivalent, or overfit
candidate. The returned result retains both the raw SDK outputs and a
serializable analysis with metric deltas, hard/critical labels, severe
regressions, and overfit status. The Gate checks validation improvement and pass
rate, protected cases, required metrics, overfitting, and configured budgets.
Reporting and source writeback remain later-stage work.

Run the deterministic stage from the repository root:

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --run-id local_stage3b \
  --scenario improve
```

The command creates:

```text
runs/local_stage3b/
└── workspace/
    └── prompts/
        └── 01_system_prompt.md
```

All paths in `pipeline.json` are relative to the example directory. Prompt
sources are never written. The candidate remains in the isolated run workspace
after evaluation so it can be inspected. `optimizer.json` remains the native
SDK optimizer configuration, while orchestration settings stay in
`pipeline.json`.

For cases with multiple runs or invocations, normalization keeps every
invocation's expected/actual response, tool calls, and metric results. Case
status and score are aggregated from the SDK's overall metrics; invocation
metrics are evidence and do not receive extra weight in that aggregate.
Attribution scans evidence from every run and invocation, retains all matching
evidence, and chooses one primary category using the fixed priority in
`attribution.py`; the other distinct matches become secondary categories.

Gate always evaluates every rule so a rejection retains all reasons. In fake
mode elapsed duration is measured, while monetary cost and token usage are
explicitly `unavailable` rather than recorded as zero. Configured unavailable
budgets follow `budget.on_unavailable`; budgets without a limit are skipped.
The built-in scenarios produce ACCEPT for `improve` and REJECT for both
`no_improvement` and `overfit`.

Run the stage-one through stage-3b tests with:

```bash
pytest -q \
  tests/evaluation/test_eval_optimize_loop_stage1.py \
  tests/evaluation/test_eval_optimize_loop_stage2.py \
  tests/evaluation/test_eval_optimize_loop_stage3a.py \
  tests/evaluation/test_eval_optimize_loop_stage3b.py
```
