# eval_optimize_loop

> Part of the `examples/optimization` series. Where [`quickstart`](../quickstart)
> drives a live `AgentOptimizer` (GEPA) against a real agent, this example focuses
> on the **closed loop around** optimization — baseline evaluation, failure
> attribution, candidate validation, an acceptance gate, and an auditable report —
> and runs fully reproducibly in fake/trace mode **without an API key**.

This example implements a reproducible Evaluation + Optimization closed loop:

```text
baseline evaluation
  -> failure attribution
  -> prompt candidate generation
  -> validation regression
  -> acceptance gate
  -> auditable JSON/Markdown report
```

Run without API keys:

```bash
# First run may spend time on a one-off `uv sync`; the loop itself is ~seconds.
uv run python examples/optimization/eval_optimize_loop/run.py
```

Set `YUN_LOG_LEVEL=DEBUG` for more verbose logs (default `INFO`).

Inputs:

- `prompts/baseline_system.md` — target prompt being optimized.
- `train.evalset.json` / `val.evalset.json` — SDK-clean evalsets (trace mode).
- `case_meta.json` — per-case `key` / `rubric` / `tool_intent` (kept out of the
  evalset so `EvalSet` stays schema-clean).
- `optimizer.json` — metric weights, scripted candidate patch, and gate thresholds.

Outputs:

- `optimization_report.json` / `optimization_report.md`
- `runs/latest/baseline_prompt.md` / `runs/latest/candidate_prompt.md`

The sample has 6 cases:

- train: two optimizable failures and one optimization-ineffective format case.
- validation: one new pass, one hard regression, and one soft degradation.

## 设计说明（四支柱）

**失败归因（阶段 2）。** 归因完全基于结构化评测信号，不依赖 case 命名。每条 case
记录三项 metric 子分（final_response / tool_trajectory / rubric）与关键轨迹（query、
expected/actual 工具与回复）。`classify_tool_failure` 据「期望轨迹 vs 实际轨迹」判类：
期望调用权威检索工具却没调用 → `knowledge_recall_insufficient`；调全了期望工具又多调
→ `spurious_tool_call`；首工具名对但参数不同 → `parameter_error`；否则 `tool_call_error`。
rubric 维度由 `case_meta.json` 显式声明（`json_format`/`no_tool`/`single_tool`），失败时
映射为 `format_error` 或 `llm_rubric_not_met`。归因只统计 baseline 失败。

**接受策略（阶段 5）。** Gate 以验证集为先，五项可配置约束全过才 ACCEPT：① 验证集均分
提升 ≥ `min_val_score_gain`；② 无新增 hard fail；③ 无「关键 case」退化（关键性由
`case_meta.json` 的 `key=true` 标记，而非把所有验证 case 一概视为关键）；④ 非过拟合
（训练涨而验证跌）；⑤ 优化成本 ≤ `max_cost_usd`。各检查相互独立，便于定位拒绝原因。

**防过拟合。** 第 ④ 项专门拦截「训练大幅提升、验证退化」：本例候选给 baseline 注入
激进检索行为，训练集 +0.53 但验证集回落，gate 据此 REJECT。关键 case 退化（③）与新增
hard fail（②）提供正交的二次保险，即便总分变化很小也能拦住有害候选。

**产物审计（阶段 6）。** `optimization_report.json` 持久化 baseline/candidate 逐 case 分数与
轨迹、逐 case delta、失败归因、gate 各检查、决策理由、成本/耗时/seed、prompt 的 SHA-256 与
config 快照；`runs/latest/` 留存 baseline 与候选 prompt 全文。`.md` 顶部由 `narrate_report`
依据 gate/delta 数据生成「人话总结」，确定性、无需模型，换输入也不会失真。SDK 桥接通过
`EvalSet.model_validate_json` 校验评测集，并用 trace-only `AgentEvaluator` 跑一次冒烟，证明
管线确实接到真实 SDK 评测器；fake/trace 模式仅在评分/优化处用确定性替身以保证无 key 可复现。
