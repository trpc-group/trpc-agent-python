# Evaluation + Optimization Loop

## 1. Purpose

This example implements the issue requirement for a reproducible Evaluation + Optimization pipeline. It is not only an `AgentOptimizer` quickstart: it wraps optimization with baseline evaluation, failure attribution, validation regression, gate decisions, and audit artifacts.

The default `fake` mode runs without model credentials. The `live` mode uses a real `LlmAgent` bridge and invokes `AgentOptimizer.optimize` against a `TargetPrompt`.

## 2. Pipeline Stages

The pipeline runs six stages:

1. Baseline evaluation: score train and validation sets separately, including metric scores, pass/fail, reasons, and key trace fields.
2. Failure attribution: cluster failures into `final_response_mismatch`, `tool_call_error`, `parameter_error`, `llm_rubric_not_met`, `knowledge_recall_insufficient`, and `format_error`.
3. Optimization execution: fake mode applies a deterministic candidate; live mode calls `AgentOptimizer.optimize` with `TargetPrompt.add_path("system_prompt", ...)`.
4. Candidate validation: rerun train and validation sets and compute per-case deltas such as `new_pass`, `new_fail`, `score_up`, and `score_down`.
5. Acceptance gate: require validation gain, no new hard fail, no key-case regression, no train-up/validation-down overfit, and cost within budget.
6. Audit persistence: write prompt snapshots, scores, deltas, gate reasons, cost, duration, seed, and config snapshots.

## 3. Directory Layout

```text
examples/optimization/eval_optimize_loop/
├── agent/
│   ├── __init__.py
│   └── agent.py
├── prompts/
│   └── system.md
├── train.evalset.json
├── val.evalset.json
├── case_meta.json
├── optimizer.json
├── optimizer.sdk.json
├── run.py
├── optimization_report.json
└── optimization_report.md
```

## 4. Inputs

- `train.evalset.json`: training evaluation set.
- `val.evalset.json`: validation evaluation set; it must be a different file from train.
- `optimizer.json`: outer-loop configuration for mode, metrics, fake candidate patch, and gate thresholds.
- `prompts/system.md`: baseline prompt source registered as the optimization target.
- `case_meta.json`: out-of-schema metadata for key cases, rubric kinds, and attribution hints.
- `optimizer.sdk.json`: live-only SDK optimizer config passed to `AgentOptimizer.optimize`.

## 5. Outputs

- `optimization_report.json`: machine-readable audit report with baseline, candidate, delta, gate, attribution, optimizer status, cost, duration, seed, and config snapshot.
- `optimization_report.md`: human-readable decision summary.
- `runs/latest/baseline_prompt.md`: exact baseline prompt snapshot.
- `runs/latest/candidate_prompt.md`: candidate prompt snapshot.
- `runs/latest/agent_optimizer/`: live-only raw SDK artifacts, including `RoundRecord`-backed round files, `result.json`, `summary.txt`, and `best_prompts/`.

## 6. Run Modes

Fake mode:

```bash
python examples/optimization/eval_optimize_loop/run.py --mode fake
```

Live mode:

```bash
set TRPC_AGENT_API_KEY=...
set TRPC_AGENT_BASE_URL=...
set TRPC_AGENT_MODEL_NAME=...
python examples/optimization/eval_optimize_loop/run.py --mode live
```

`fake` mode uses a deterministic fake model, fake judge, and scripted candidate so the full loop runs without API keys. `live` mode uses `agent/agent.py`, creates a fresh `LlmAgent` for each call, and invokes `AgentOptimizer.optimize`.

## 7. Customizing The Agent

Edit `agent/agent.py` when connecting a real business agent.

Key constraints:

- `make_call_agent(prompt_path)` must return an async function with the exact optimizer contract `async (query: str) -> str`.
- `create_agent(prompt_path)` must re-read the prompt file every time so candidates written by `AgentOptimizer` take effect immediately.
- `TargetPrompt.add_path("system_prompt", path)` must point to the same prompt file that the agent actually reads.
- For HTTP, CLI, remote config, or multi-agent pipelines, keep the outer contract the same and replace only the bridge implementation.

The outer report still computes richer trace-style scoring. The SDK optimizer itself receives final-text responses through `call_agent`, so `optimizer.sdk.json` intentionally avoids metrics that require full session traces.

## 8. Design And Validation

Failure attribution is rule-based over structured signals, not case ids. Each case records final response, tool trajectory, rubric sub-scores, and expected/actual tool calls. Rubric failures map to `format_error` or `llm_rubric_not_met`; tool mismatches map to tool, parameter, spurious-call, or knowledge-recall categories.

The gate is validation-first. A candidate is accepted only if validation mean improves by the configured threshold, no new hard fail appears, key validation cases do not regress, train improvement does not coincide with validation loss, and cost is within budget.

The bundled fake candidate intentionally improves two train cases and one validation case while damaging two key validation cases. The expected sample decision is `REJECT`, demonstrating overfit rejection.

Verified fake command:

```bash
C:\Users\27303\PycharmProjects\Yun\.venv\Scripts\python.exe examples\optimization\eval_optimize_loop\run.py --mode fake
```

Observed sample result:

```text
train: 0.25 -> 0.7833
validation: 0.7333 -> 0.6667
decision: REJECT
```

Known limits: live mode requires SDK dependencies plus `TRPC_AGENT_API_KEY`, `TRPC_AGENT_BASE_URL`, and `TRPC_AGENT_MODEL_NAME`; no-key environments should use `--mode fake`.
