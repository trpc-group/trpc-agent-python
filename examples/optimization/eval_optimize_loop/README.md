# Evaluation + Optimization Loop — Stage 1

This example is the foundation for an auditable evaluation and prompt-
optimization loop. Stage 1 validates the complete pipeline configuration and
inputs, snapshots source prompts, and creates an isolated path-backed
`TargetPrompt` under a unique run directory.

It deliberately does not run evaluation, candidate generation, gating,
reporting, or source writeback yet. Later stages consume the stable
`PreparedRun` returned by `pipeline.prepare_run`.

Run the preparation step from the repository root:

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --run-id local_stage1
```

The command creates:

```text
runs/local_stage1/
└── workspace/
    └── prompts/
        └── 01_system_prompt.md
```

All paths in `pipeline.json` are relative to the example directory. They may
not escape that directory. Prompt sources must be unique, regular UTF-8 files;
the preparation step never writes them. `optimizer.json` remains the native
SDK optimizer configuration, while gate, budget, artifact, and writeback
settings stay in `pipeline.json`.

Run the stage-one test suite with:

```bash
pytest -q tests/evaluation/test_eval_optimize_loop_stage1.py
```
