# Evaluation + Optimization Closed Loop

This example implements a reproducible evaluation and prompt-optimization loop
that runs without `TRPC_AGENT_API_KEY` or any external model provider. It is an
example-local implementation: the code mirrors the evaluator/optimizer workflow
with a thin adapter so the behavior is deterministic and easy to review.

## Architecture

```text
train.evalset.json      val.evalset.json       baseline_system_prompt.txt
        |                       |                         |
        v                       v                         v
   loader.py --------------> evaluator.py <----------- fake_model.py
        |                       |                         |
        |                       v                         |
        |                  fake_judge.py                  |
        |                       |                         |
        v                       v                         |
 attribution.py <------ baseline + candidate results     |
        |                       |                         |
        v                       v                         |
   optimizer.py --------> candidate prompts --------------+
        |                       |
        v                       v
     gate.py ------------> accept/reject decisions
        |
        v
     report.py ----------> optimization_report.json/.md
```

## Quick Start

Short deterministic command:

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py --fake-model --fake-judge --trace
```

Full command with explicit inputs:

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --train examples/optimization/eval_optimize_loop/data/train.evalset.json \
  --val examples/optimization/eval_optimize_loop/data/val.evalset.json \
  --optimizer-config examples/optimization/eval_optimize_loop/data/optimizer.json \
  --prompt examples/optimization/eval_optimize_loop/prompts/baseline_system_prompt.txt \
  --output-dir /tmp/eval-optimize-loop \
  --fake-model \
  --fake-judge \
  --trace
```

The default output directory is the system temp directory,
`eval-optimize-loop`. The fixed checked-in examples are:

- `outputs/optimization_report.example.json`
- `outputs/optimization_report.example.md`

## What The Example Demonstrates

- Baseline evaluation on train and validation evalsets.
- Rule-based failure attribution for failed cases.
- A fake optimizer that proposes two candidates:
  - `candidate_001_overfit`: improves train score but regresses validation.
  - `candidate_002_safe`: improves validation without protected regression.
- Candidate validation on the full validation set.
- A configurable gate that rejects train-only gains and protected-case
  regressions.
- Audit persistence with seed, cost, deterministic duration, config hash, and
  candidate prompt text.

The six eval cases cover:

- optimization succeeds: `candidate_002_safe` is accepted;
- optimization has no effect: rubric cases stay unchanged;
- optimization regresses/overfits: `candidate_001_overfit` forces JSON on
  validation prose and protected exact-answer cases.

## Fake Model, Fake Judge, And Trace Mode

`--fake-model` uses deterministic prompt markers to simulate baseline, overfit,
and safe candidate behavior. `--fake-judge` scores JSON, exact-answer, and
rubric cases locally. `--trace` stores per-case fake model and judge decisions
inside `optimization_report.json`, which makes failures reviewable without
calling an external service.

This example currently requires `--fake-model --fake-judge`. That is deliberate:
it keeps the PR deterministic and ensures the example runs in CI without API
keys.

## Inspecting Reports

Open `optimization_report.md` first for the human-readable decision:

- final selected candidate;
- why the overfit candidate was rejected;
- why the safe candidate was accepted;
- baseline vs candidate score table;
- per-case delta table;
- failure attribution summary;
- prompt diffs;
- reproducibility command.

Use `optimization_report.json` for automation and audit. It includes the same
decision data plus full case outputs, traces, costs, config hash, and candidate
prompt snapshots.

## Run Tests

```bash
python -m pytest examples/optimization/eval_optimize_loop/tests
```

The tests cover gate rejection/acceptance, failure attribution, fake-mode report
generation, and deterministic output with the same seed.

## Switching To Real Evaluator/Optimizer Later

The example intentionally isolates integration points:

- replace `ExampleEvaluator` in `eval_loop/evaluator.py` with an adapter around
  `AgentEvaluator` when real agent execution and SDK evalsets are desired;
- replace `FakeOptimizer` in `eval_loop/optimizer.py` with `AgentOptimizer` or a
  remote optimizer;
- keep `gate.py`, `attribution.py`, and `report.py` as reviewable policy and
  audit layers around the real components.

When switching to real mode, keep train and validation files distinct, preserve
the protected-case gate, and continue writing both JSON and Markdown reports so
optimization decisions remain reviewable.
