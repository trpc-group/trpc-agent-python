# Evaluation + Optimization closed loop

This example implements a reproducible "evaluate -> attribute failures -> optimize prompt -> validate candidate -> gate -> audit" loop for issue #91.

It is intentionally runnable without a real API key. The default backend is deterministic fake/trace mode: it generates baseline and candidate traces, evaluates both with `AgentEvaluator`, applies the acceptance gate, and writes `optimization_report.json` plus `optimization_report.md`. The fake trace model recognizes the public sample IDs and also has query/trace-based fallbacks for JSON-format, tool-argument, private-knowledge, and validation-regression cases, so the gate logic is not tied only to the checked-in IDs.

The same script also supports `--backend agent_optimizer`. That path calls `AgentOptimizer.optimize(...)` against the configured `TargetPrompt` files and uses the optimized prompt text to regenerate candidate traces before the final validation/gate/report stages.

## Files

```text
examples/optimization/eval_optimize_loop/
|-- run_pipeline.py
|-- optimizer.json
|-- train.evalset.json
|-- val.evalset.json
|-- optimization_report.json
|-- optimization_report.md
|-- prompts/
|   |-- system.md
|   `-- skill.md
`-- trace_evalsets/
    |-- baseline_train.evalset.json
    |-- baseline_val.evalset.json
    |-- candidate_train.evalset.json
    `-- candidate_val.evalset.json
```

## Run offline

From the repository root:

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py
```

If `python` is not on PATH, use your active interpreter, for example:

```bash
py -3.14 examples/optimization/eval_optimize_loop/run_pipeline.py
```

The script writes reports into this directory by default. The expected gate decision for the public sample is `reject`: the candidate improves the training split and one validation case, but introduces validation regressions, including a critical case.

## Data design

The sample has six public cases:

- `train_format_json`: optimizable response-format failure.
- `train_tool_args`: optimizable tool-argument failure.
- `train_knowledge_gap`: optimization is ineffective because missing private knowledge cannot be fixed by prompt wording alone.
- `val_format_json`: validation case that improves.
- `val_critical_discount`: validation hard regression and critical-case regression.
- `val_stable_refund`: validation regression caused by overfitting.

## Report contract

`optimization_report.json` includes:

- `baseline`: train/validation scores, pass/fail, metric scores, failure reasons, and traces.
- `optimization`: every round's input failure attribution, full candidate prompts, token usage, model-call count, seed, and cost.
- `candidate`: train/validation scores after the candidate.
- `delta`: per-case deltas, new passes, new failures, score improvements, and score regressions.
- `gate_decision`: accept/reject decision with every configured gate check and reason.
- `failure_attribution`: failure-type counts for every phase.
- `audit`: reproducibility config and generated trace evalset artifacts.

## Optional real optimizer backend

The checked-in example does not store secrets. To run the real optimizer backend, pass credentials through environment variables and keep `optimizer.json` using environment references.

For the DeepSeek OpenAI-compatible endpoint:

```powershell
$env:TRPC_AGENT_OPT_BASE_URL="https://api.deepseek.com/v1"
$env:TRPC_AGENT_OPT_MODEL="deepseek-v4-pro"
$env:TRPC_AGENT_OPT_API_KEY="<your key>"
py -3.14 examples/optimization/eval_optimize_loop/run_pipeline.py --backend agent_optimizer
```

The optimizer stage uses response-only metrics because `AgentOptimizer` runs through a black-box `call_agent` callback. The final report still reruns trace-mode baseline/candidate validation with the full metric set, including tool trajectory checks. The default `backend` remains `fake` so CI and reviewer machines can run without network access.
