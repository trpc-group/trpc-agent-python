# Evaluation + Optimization Loop — Stage 2

This example now provides the deterministic offline foundation for an auditable
evaluation and prompt-optimization loop. Stage 1 validates inputs and creates
an isolated prompt workspace. Stage 2 evaluates that baseline on train and
validation data, generates a fake prompt candidate, and evaluates the candidate
on both datasets again.

No model, API key, judge, or optimizer is used. The fake agent reads explicit
capability rules from the current working prompt, while the fake candidate
provider can generate an improving, behaviorally equivalent, or overfit
candidate. Gate, reporting, and source writeback remain later-stage work.

Run the deterministic stage from the repository root:

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --run-id local_stage2 \
  --scenario improve
```

The command creates:

```text
runs/local_stage2/
└── workspace/
    └── prompts/
        └── 01_system_prompt.md
```

All paths in `pipeline.json` are relative to the example directory. Prompt
sources are never written. The candidate remains in the isolated run workspace
after evaluation so it can be inspected. `optimizer.json` remains the native
SDK optimizer configuration, while orchestration settings stay in
`pipeline.json`.

Run the stage-one and stage-two tests with:

```bash
pytest -q \
  tests/evaluation/test_eval_optimize_loop_stage1.py \
  tests/evaluation/test_eval_optimize_loop_stage2.py
```
