# eval_optimize_loop — Evaluation + Optimization 自动闭环

> 对应 [issue #91](https://github.com/trpc-group/trpc-agent-python/issues/91)
> 构建「评测 → 失败归因 → prompt 优化 → 验证集回归 → 接受决策 → 审计落盘」的可复现闭环。

把一次 prompt 优化从「分数变高了」升级为**可审计的发布决策**：不只跑 `AgentOptimizer`，
而是独立复评每个候选、检测过拟合、给出 accept/reject 决策与理由。

## 闭环流程

```
baseline prompt + train.evalset + val.evalset + optimizer.json + gate.json
   │
   ① Baseline 评测（AgentEvaluator，train/val 分别打分）
   ② 失败归因（分层：规则快通道 + 反事实深归因）
   ③ 优化执行（fake: 三候选 fixture；online: 真实 GEPA）
   ④ 候选验证（逐 case delta：new_pass/new_fail/improved/regressed/unchanged）
   ⑤ Gate 决策（三态 + 过拟合三重检测）
   ⑥ 审计落盘（optimization_report.json + .md + audit/*）
   ▼
退出码 0=accept / 2=reject / 1=出错
```

## 快速开始（无需 API key）

```bash
# 在本目录下，用仓库 venv
python run_pipeline.py --mode fake
```

产出 `sample_output/optimization_report.json`（结构化）+ `.md`（人读）+ `audit/`（审计快照）。
fake 模式全程确定性、无 LLM 调用，6 case 三类场景在 < 1s 内跑完。

## 三种模式

| 模式 | 评测方式 | 需要 API key | 用途 |
|---|---|---|---|
| `fake` | trace 回放 + 预录制 variant actual | 否 | **默认**，演示三类场景、验收基线 |
| `trace` | 同 fake（确定性 trace 回放） | 否 | CI 回归基线 |
| `online` | 真实 `AgentOptimizer` + `call_agent` | 是 | 真实业务优化 |

fake/trace 用两个**确定性、无 LLM** 的 SDK evaluator：`final_response_avg_score`（contains）
和 `tool_trajectory_avg_score`（exact）。三候选（robust/ineffective/overfit）的 actual 在
`offline/fixtures.py` 预录制，让「改 prompt 真改评测结果」可确定性复现。

## 三类场景（6 case，3 训练 + 3 验证）

| 候选 | train | val | gate | 说明 |
|---|---|---|---|---|
| **robust** | 全通过 | 全通过 | **accept** | JSON 格式 + 正确分类 + 馆藏查询全修复 |
| **ineffective** | = baseline | = baseline | **reject**(tie) | 候选与 baseline 等价，无任何提升 |
| **overfit** | 全通过 | critical 退化 | **reject**(overfit) | 修了 train 能力但把图书查询一律错归到 history |

## 目录结构

```
eval_optimize_loop/
├── run_pipeline.py          # CLI 入口（--mode fake|trace|online）
├── optimizer.json           # GEPA 优化配置 + metric 配置
├── gate.json                # 可配置接受策略阈值
├── pipeline/                # 闭环外层（模式无关）
│   ├── models.py            # pydantic 数据结构（extra=forbid）
│   ├── config.py            # 配置加载 + sha256
│   ├── evaluator.py         # AgentEvaluator 封装 + 归一化
│   ├── comparator.py        # 逐 case delta（5 桶）
│   ├── attribution.py       # 分层失败归因（规则 + 反事实）
│   ├── gate.py              # 三态决策 + 过拟合三重检测
│   └── reporting.py         # report.json + .md + audit
├── offline/fixtures.py      # 6 case × 4 variant 的预录制 actual（fake/trace 用）
├── agent/                   # online 模式被测 agent（真实 LlmAgent + call_agent）
├── data/{train,val}.evalset.json   # 样例评测集（expected）
└── tests/test_eval_optimize_loop.py
```

## 配置

**`gate.json`**（接受策略，全部阈值外置）：
```jsonc
{
  "min_validation_score_delta": 0.05,   // val 提升下限
  "max_new_hard_fails": 0,              // 禁止新增 hard fail
  "critical_case_ids": ["val_fiction_key"],  // 关键 case 不许退化
  "overfitting": { "generalization_gap_threshold": 0.1 },
  "budget": { "max_duration_seconds": 180, "cost_measurement": "measured_zero_offline" },
  "tie_policy": "reject"
}
```

**`optimizer.json`**：SDK `AgentOptimizer` 标准 GEPA 配置 + `evaluate.metrics`（fake/online 共用）。

## 运行测试

```bash
python -m pytest tests/ -v
```

覆盖：三类场景决策、过拟合检测、归因 coverage/准确率、≤3 分钟、报告字段、隐藏样本归因、CLI 退出码。

## online 模式

需配置 `TRPC_AGENT_API_KEY` / `TRPC_AGENT_BASE_URL` / `TRPC_AGENT_MODEL_NAME`，然后：

```bash
python run_pipeline.py --mode online
```

online 调用真实 `AgentOptimizer.optimize`（GEPA 反思优化），`agent/agent.py` 的 `call_agent`
每次重读 `system.md`（prompt 热加载），候选 prompt 真实改变 agent 行为。SDK 原生 `OptimizeResult`
（含 baseline/best pass_rate、每轮候选、cost）写入 `sample_output/online_run/`。

> 完整的 gate + 自定义 report 闭环（含逐 case delta、独立 trace 复评）在 fake/trace 模式
> 已完整演示并可无 key 验证；online 接入真实业务时，把 `agent/` 换成业务 agent、
> `data/` 换成业务评测集即可复用同一套 pipeline 外层。

## 方案设计说明（~400 字）

本闭环的核心是**不信任优化器自报分**，在 `AgentOptimizer` 之上叠加独立编排层。六个阶段对应
issue 要求，其中三个关键设计决定了能否通过验收：

1. **分层失败归因**（`attribution.py`）：规则引擎做快通道，从 actual/expected 的工具轨迹与
   response 差异直接归因（覆盖 format/tool/parameter/knowledge/mismatch）；规则未命中或信号弱时
   才触发反事实干预——单变量替换（只换 response 或只换 tools）重评，用因果证据兜底。反事实用
   本地纯 Python 复刻 metric（contains + trajectory exact），零 API 成本。这是「归因准确率 ≥75%」
   与「全流程 ≤3 分钟」两个看似冲突验收点的破局点：多数 case 走快通道，疑难 case 才付成本。

2. **过拟合三重检测**（`gate.py`）：显式公式 `train↑ 且 val↓`、泛化缺口
   `train_delta - val_delta > 阈值`（仅在 val 未达标时触发，避免误伤健康候选）、多轮趋势背离。
   配合 critical case 回归检查，确保「val 退化但 train 提升」的候选必被拒绝。

3. **确定性可复现**（`offline/fixtures.py` + `reporting.py`）：fake/trace 用预录制 variant actual
   + 两个无 LLM 的 SDK evaluator，全程确定性；落盘用原子写 + sha256 摘要 config/evalset/prompt，
   `cost.measurement` 三态区分（unavailable / measured_zero_offline / measured_from_replay），
   未知成本 fail-closed。

Gate 用可配置 AND 规则、三态输出（accept/reject/needs_review），每条 check 带 actual/expected/reason，
退出码 0/2 区分接受/拒绝供 CI 使用。

## issue #91 验收对照

| 验收点 | 落地 |
|---|---|
| 6 case 全可运行 + 完整报告 | `--mode fake` 产出 report.json/md |
| 决策准确率 ≥80% | 过拟合三重检测 + gate 规则；tests 含隐藏样本 |
| val 退化 train 提升必拒绝 | `gate.py` explicit overfit + critical regression |
| 归因准确率 ≥75% + 每 case ≥1 原因 | 分层归因 + coverage_rate；tests 断言 |
| fake/trace ≤3 分钟 | 确定性 metric 无 LLM，实测 < 1s |
| 报告含 baseline/candidate/delta/gate/理由 | `optimization_report.json` schema |
