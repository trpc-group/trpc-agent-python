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
6. Audit persistence: write prompt snapshots, scores, deltas, gate reasons, cost, duration, and config snapshots into an append-only per-run directory.

## 3. Directory Layout

```text
examples/optimization/eval_optimize_loop/
├── agent/
│   ├── __init__.py
│   └── agent.py
├── prompts/
│   └── system.md
├── sample_output/
│   ├── optimization_report.sample.json
│   └── optimization_report.sample.md
├── tests/
│   ├── __init__.py
│   └── test_pipeline_units.py
├── train.evalset.json
├── val.evalset.json
├── case_meta.json
├── optimizer.json
├── optimizer.sdk.json
└── run.py
```

## 4. Inputs

- `train.evalset.json`: training evaluation set.
- `val.evalset.json`: validation evaluation set; it must be a different file from train.
- `optimizer.json`: outer-loop configuration for mode, metric weights, fake candidate patch, and gate thresholds. It is validated at startup (`validate_config`): the three metric weights must sum to 1.0 and all gate keys must be present.
- `prompts/system.md`: baseline prompt source registered as the optimization target.
- `case_meta.json`: out-of-schema metadata for key cases, rubric kinds, and attribution hints. The `category` field declares the expected failure category per case; the report's attribution self-check measures rule-based attribution accuracy against it.
- `optimizer.sdk.json`: live-only SDK optimizer config passed to `AgentOptimizer.optimize`. This is also where the GEPA `seed` and the in-run spend cap (`max_metric_calls`) live.

## 5. Outputs

All outputs are generated at runtime and gitignored; a frozen sample report is committed under `sample_output/` for reference.

- `runs/<timestamp>_<run_id>/`: append-only per-run audit directory containing `baseline_prompt.md`, `candidate_prompt.md`, `optimization_report.json`, `optimization_report.md`, and (live only) raw SDK artifacts under `agent_optimizer/` (`RoundRecord`-backed round files, `result.json`, `summary.txt`, `best_prompts/`).
- `runs/latest/`: convenience mirror of the newest run directory.
- `optimization_report.json` / `optimization_report.md`: convenience copies of the newest report at the example root.

The JSON report records baseline/candidate per-case scores and traces, per-case deltas, failure attribution with an accuracy self-check against the expected categories in `case_meta.json`, every gate check with its reason, decision, cost split into optimizer and evaluation spend, token counts, duration, prompt SHA-256 hashes, the GEPA seed, and a full config snapshot.

## 6. Run Modes

Fake mode (no credentials, deterministic):

```bash
python examples/optimization/eval_optimize_loop/run.py --mode fake
```

Live mode:

```bash
# Linux / macOS
export TRPC_AGENT_API_KEY=...
export TRPC_AGENT_BASE_URL=...
export TRPC_AGENT_MODEL_NAME=...

# Windows (PowerShell): $env:TRPC_AGENT_API_KEY = "..."; etc.
python examples/optimization/eval_optimize_loop/run.py --mode live
```

`fake` mode uses a deterministic fake model, fake judge, and scripted candidate so the full loop runs without API keys and with zero network calls. `live` mode uses `agent/agent.py`, creates a fresh `LlmAgent` for each call, and invokes `AgentOptimizer.optimize`.

Environment variables:

| Variable | Default | Meaning |
|----------|---------|---------|
| `EVAL_OPT_LOG_LEVEL` | `INFO` | Log verbosity for the pipeline logger. |
| `EVAL_OPT_USD_PER_1M_TOKENS` | `1.0` | USD price per 1M tokens used to estimate live evaluation cost. |
| `EVAL_OPT_CALL_TIMEOUT` | `120` | Per-call timeout (seconds) for live agent calls. |
| `EVAL_OPT_CALL_ATTEMPTS` | `3` | Max attempts per live agent call (exponential backoff between retries). |
| `EVAL_OPT_CALL_BACKOFF` | `1.0` | Backoff base in seconds (delay = base * 2^attempt + jitter). |

## 7. Cost Accounting And Its Limits

The audit report splits spend into optimizer cost (reported by `AgentOptimizer`) and evaluation cost (estimated from the tokens accumulated across the four baseline/candidate evaluation passes at the `EVAL_OPT_USD_PER_1M_TOKENS` rate). The `cost_budget` gate checks the total.

Be aware of what the gate is and is not: it is a **post-hoc audit check** — it can reject a candidate whose search cost exceeded budget, but the money is already spent by then. The in-run spend cap for live mode is `max_metric_calls` in `optimizer.sdk.json`; size it to your budget before launching a live run.

## 8. Customizing The Agent

Edit `agent/agent.py` when connecting a real business agent.

Key constraints:

- `make_call_agent(prompt_path)` must return an async function with the exact optimizer contract `async (query: str) -> str`.
- `create_agent(prompt_path)` must re-read the prompt file every time so candidates written by `AgentOptimizer` take effect immediately.
- `TargetPrompt.add_path("system_prompt", path)` must point to the same prompt file that the agent actually reads.
- For HTTP, CLI, remote config, or multi-agent pipelines, keep the outer contract the same and replace only the bridge implementation.

The outer report still computes richer trace-style scoring. The SDK optimizer itself receives final-text responses through `call_agent`, so `optimizer.sdk.json` intentionally avoids metrics that require full session traces.

## 9. Design And Validation

Failure attribution is rule-based over structured signals, not case ids. Each case records final response, tool trajectory, rubric sub-scores, and expected/actual tool calls. Rubric failures map to `format_error` or `llm_rubric_not_met`; tool mismatches map to tool, parameter, spurious-call, or knowledge-recall categories.

The gate is validation-first. A candidate is accepted only if validation mean improves by the configured threshold, no new hard fail appears, key validation cases do not regress, train improvement does not coincide with validation loss, and cost is within budget.

The bundled fake candidate intentionally improves two train cases and one validation case while damaging two key validation cases. The expected sample decision is `REJECT`, demonstrating overfit rejection:

```bash
python examples/optimization/eval_optimize_loop/run.py --mode fake
```

```text
train: 0.25 -> 0.7833
validation: 0.7333 -> 0.6667
decision: REJECT
```

## 10. 方案设计说明

**失败归因。** 归因完全基于结构化评测信号而非 case 命名：每条 case 记录 final_response、tool_trajectory、rubric 三个子分与期望/实际工具轨迹。工具轨迹不匹配时按规则判类——期望依赖权威检索工具（`case_meta.json` 的 `authoritative_tool`）却未调用，记为知识召回不足；调全期望工具又额外多调，记为多余工具调用；首个工具同名但参数不同，记为参数错误；其余记为工具调用错误。rubric 失败按声明维度映射为格式错误或 rubric 不达标。`case_meta.json` 为每条 case 声明期望类别 `category`，报告内置归因自检，样例 4 条失败 case 全部归因正确。

**接受策略。** gate 以验证集为先，五项可配置检查全部通过才接受候选：验证集均分提升达到阈值、无新增 hard fail、关键 case（`key=true`）不退化、非过拟合、总成本（优化器花费加按 token 估算的评测花费）不超预算。各检查相互独立，拒绝理由逐项落盘，便于定位。

**防过拟合。** 训练集与验证集物理分离且启动时校验为不同文件；第四项检查专门拦截"训练集提升、验证集下降"的候选；关键 case 退化与新增 hard fail 两项提供正交的二次保险，即便总分变化很小也能拦住有害候选。随附样例即演示该场景：候选使训练集 +0.53 但验证集回落，gate 正确拒绝。

**产物审计。** 每次运行写入独立的 `runs/<时间戳>_<run_id>/` 目录（append-only，历史不被覆盖），含 baseline 与候选 prompt 快照、JSON/Markdown 双报告；报告记录逐 case 分数与轨迹、逐 case delta、归因统计与自检准确率、每项 gate 检查与决策理由、成本与 token 的优化器/评测拆分、耗时、GEPA 随机种子、prompt 的 SHA-256 以及完整配置快照。run_id 同时注入日志行，跨产物可对齐追溯。

## 11. Tests

The attribution rules, rubric scorer, gate checks, case diffing, and config validation are covered by IO-free unit tests:

```bash
python -m pytest examples/optimization/eval_optimize_loop/tests -q
```

Known limits: live mode requires SDK dependencies plus `TRPC_AGENT_API_KEY`, `TRPC_AGENT_BASE_URL`, and `TRPC_AGENT_MODEL_NAME`; no-key environments should use `--mode fake`. The live retry logic matches rate-limit errors by exception type name because provider SDK exception classes are intentionally not imported here.
