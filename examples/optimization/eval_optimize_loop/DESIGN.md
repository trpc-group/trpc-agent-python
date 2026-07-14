# Evaluation + Optimization 自动闭环 Pipeline 设计文档

> 对应 [issue #91](https://github.com/trpc-group/trpc-agent-python/issues/91)
> 主题：构建「评测 → 失败归因 → prompt 优化 → 验证集回归 → 接受决策 → 审计落盘」的自动闭环
> 分支：`feat/eval-optimization-loop-91`

---

## 0. 文档定位

本文是实现的**架构契约**：定义闭环的边界、各阶段职责、关键数据结构与决策算法。
读完本文应能回答：每个文件放什么、issue 的 6 条验收标准分别落在哪段代码、为什么这样取舍。

本文**不是**最终 README。README 面向使用者（怎么跑），DESIGN 面向评审者（为什么这么设计）。
issue 要求的「300–500 字方案设计说明」将从本文 §4 浓缩进 README。

---

## 1. 背景与目标

### 1.1 issue 核心诉求

把一次 prompt 优化从「分数变高了」升级为**可审计的发布决策**。核心不是再跑一次 `AgentOptimizer`，
而是回答四个问题：

1. 优化**真的**提升了吗？（不能只信优化器自报的聚合分）
2. 提升是否**牺牲了其他指标**？（验证集逐 case 回归）
3. 是否**过拟合**？（train 升 val 降）
4. 改出来的 prompt**值得回写源文件**吗？（接受/拒绝 + 理由）

### 1.2 与现有 SDK 能力的关系：编排层，不是重写

SDK 已提供的能力（**复用，不重写**）：

| SDK 能力 | 位置 | 在闭环中的角色 |
|---|---|---|
| `AgentOptimizer.optimize()` | [_agent_optimizer.py:132](../../../trpc_agent_sdk/evaluation/_agent_optimizer.py#L132) | 优化引擎，内部已做 train/val 双集 + GEPA 反思 + 早停 |
| `OptimizeResult` / `RoundRecord` | [_optimize_result.py](../../../trpc_agent_sdk/evaluation/_optimize_result.py) | 审计原料（baseline/best 分数、每轮候选、cost、duration、seed）|
| `AgentEvaluator` (trace-only) | [_agent_evaluator.py](../../../trpc_agent_sdk/evaluation/_agent_evaluator.py) | 候选独立复评（不信任优化器聚合分）|
| `TargetPrompt` | [_target_prompt.py](../../../trpc_agent_sdk/evaluation/_target_prompt.py) | prompt 源注册 + 回写 |
| `eval_mode="trace"` | [_eval_case.py:170](../../../trpc_agent_sdk/evaluation/_eval_case.py#L170) | 无 API key 回放 |
| `LLM_EVALUATOR_REGISTRY` | [_llm_evaluator.py:178](../../../trpc_agent_sdk/evaluation/_llm_evaluator.py#L178) | fake judge 注入点 |
| `call_agent` 参数 | `AgentEvaluator` / `AgentOptimizer` 公开签名 | prompt 敏感 fake model 注入点 |

**issue 真正要新写的「闭环外层」**：失败归因分类器、逐 case delta 对比、可配置 gate 决策、report 生成、
trace 化样例 case、prompt 敏感 fake model。这些 SDK 都没有。

---

## 2. 方法论背景

评测-优化闭环在工程实践上有一些常见做法，本节提炼其共识与分歧，作为本设计的立论基础。

### 2.1 设计共识（已收敛 → 直接沿用）

1. **六阶段闭环骨架**一致：baseline 评测 → 失败归因 → 优化执行 → 验证集回归 → gate 决策 → 审计落盘。
2. **失败 taxonomy 高度重合**（6–11 类，核心同名）：`format_violation` / `final_response_mismatch` /
   `tool_call_error` / `parameter_error` / `knowledge_recall_insufficient` / `llm_rubric_not_met`。
3. **过拟合检测公式收敛**：`train_delta > 0 and val_delta <= 0`。
4. **gate 四要素一致**：val 提升阈值 + 禁止新增 hard fail + 过拟合检测 + 成本/耗时预算。
5. **fake 只 mock 最底层**（`call_agent` / model registry），让 Evaluator / gate / report 走真实代码路径——
   这是「无 API key 也能端到端跑通且结果可信」的关键，不 mock 高层。
6. **report 公共字段一致**：`schema_version`、per-case delta 枚举（`new_pass/new_fail/improved/regressed/unchanged`）、
   gate decision、prompt 审计（sha256 + diff）。
7. **三类场景构造**一致：可优化成功 / 优化无效 / 优化后退化。

### 2.2 关键分歧与各方案取舍

| 维度 | 选项（代表 PR） | 各方案问题 |
|---|---|---|
| **归因方法** | 纯规则 / 全反事实 / LLM 裁判 | 规则浅且依赖 expected JSON 字段；反事实慢（每 case 多次 evaluator 调用）；LLM 裁判需 API |
| **候选可信度** | 多数信优化器自报分；或独立复评 | 信优化器自报存在「优化器自吹」偏差 |
| **模块化程度** | 单文件 / 5 模块 / 12+ 模块 | 单文件不可维护；12+ 模块对 example 过度工程 |
| **过拟合检测** | 隐式 val 门槛 / 显式公式 / 双保险 | 隐式不够明确；单公式漏「train 微升 val 平」的泛化缺口 |
| **候选应用安全** | 不改源 / 临时改写源再还原 | 临时改写在中断/并发下有还原风险 |
| **fake 策略** | 静态 fixture 回放 / prompt 敏感生成器 | 静态 fixture 下「改 prompt」不会真改行为，优化是假象 |
| **val 阈值** | 0.01 / 0.05 / 0.1 | 差异大，需参数化 |
| **CI 友好** | 普通退出 / 退出码 0=接受 2=拒绝 | 普通退出无法区分「拒绝」与「出错」|

### 2.3 本设计的整合策略

**以可信与可审计为第一原则**，吸收各 PR 最强的一环，并做三处整合创新（§4）：

- 骨架与归因/gate：自研分层归因（§4.1，创新）+ 显式过拟合三重检测（§4.3）
- 候选可信度：从 OptimizeResult.rounds 取候选 + 独立 trace 复评（不信任优化器自报分）
- 候选安全：PromptSandbox 隔离（候选只写临时目录，不污染源文件）
- fake：trace + 预录制 variant，确定性无 LLM
- 审计：sha256 全量溯源 + 三态成本 + env snapshot
- CI：退出码 0/2 区分接受/拒绝
- 模块化：中等粒度（~8 文件），兼顾可读性与不过度工程

---

## 3. 整体架构

### 3.1 闭环流程

```
                ┌──────────── 输入 ────────────┐
                │  train.evalset.json (3 case) │
                │  val.evalset.json   (3 case) │
                │  optimizer.json (GEPA 配置)  │
                │  gate.json      (决策阈值)   │
                │  prompts/system.md (baseline)│
                └───────────────┬──────────────┘
                                ▼
   ① Baseline 评测  (AgentEvaluator, train+val 全量)
                                ▼
   ② 失败归因       (分层：规则快通道 + 反事实深归因)  ★
                                ▼
   ③ 优化执行       (AgentOptimizer.optimize, GEPA)
        │           └─ _ProposalCapture 订阅 on_proposal_end
        ▼              捕获所有候选(含被 GEPA 丢弃的)
   ④ 候选独立复评   (trace-only AgentEvaluator, 去重后逐个)  ★
        │           └─ 不信任优化器聚合分
        ▼
   ⑤ 逐 case delta  (baseline vs candidate, 按 eval_id join)
                                ▼
   ⑥ Gate 决策      (可配置 AND 规则, 三态: accept/reject/needs_review)
        │           └─ 过拟合三重检测
        ▼
   ⑦ 审计落盘       (optimization_report.json + .md + audit/*.json)
                                ▼
   退出码 0(接受) / 2(拒绝) / 1(出错)
```

### 3.2 三运行模式

| 模式 | call_agent | judge | 用途 | 耗时目标 |
|---|---|---|---|---|
| **fake** | prompt 敏感 `FakeCallAgent`（读能力标记决定输出） | `fake_rubric` scorer（注册到 `LLM_EVALUATOR_REGISTRY`）| 默认，无 API key，验收「≤3 分钟」 | <3min |
| **trace** | 不调用（`eval_mode="trace"` + 预录制 `actual_conversation`）| 同 fake | 确定性回放，CI 回归基线 | <1min |
| **online** | 真实 `agent_module` | 真实 judge model | 真实优化，需 `TRPC_AGENT_API_KEY` 等 env | 不限 |

三模式共用同一套闭环代码（归因/gate/report），只替换最底层注入。fake/trace 满足「无 key 跑通」验收；
online 是真实业务接入路径。

### 3.3 目录结构

```
examples/optimization/eval_optimize_loop/
├── README.md                     # 使用说明 + 300-500 字方案设计摘要
├── DESIGN.md                     # 本文档
├── run_pipeline.py               # CLI 入口（--mode fake|trace|online）
├── optimizer.json                # GEPA 优化配置
├── gate.json                     # 可配置 gate 阈值
├── pipeline/                     # 闭环外层（模式无关）
│   ├── __init__.py
│   ├── models.py                 # pydantic 数据结构（extra="forbid"）
│   ├── config.py                 # 配置加载+校验+sha256 摘要
│   ├── attribution.py            # ★ 分层失败归因
│   ├── comparator.py             # 逐 case delta
│   ├── gate.py                   # gate 决策（三态 + 过拟合三重检测）
│   ├── sandbox.py                # PromptSandbox 候选隔离
│   └── reporting.py              # report.json + .md + audit/*.json
├── offline/                      # fake 模式注入件
│   ├── __init__.py
│   ├── call_agent.py             # prompt 敏感 FakeCallAgent
│   ├── judge.py                  # fake_rubric scorer
│   └── candidates.py             # 三类候选 prompt 样例
├── agent/                        # online 模式被测 agent
│   ├── __init__.py
│   ├── agent.py
│   ├── config.py
│   └── prompts/system.md         # baseline prompt（故意留改进空间）
├── data/
│   ├── train.evalset.json        # 3 条
│   └── val.evalset.json          # 3 条（含 1 条 critical）
├── sample_output/
│   └── optimization_report.json  # 示例报告
└── tests/
    └── test_eval_optimize_loop.py
```

**模块化原则**：pipeline/ 是模式无关的纯逻辑（可单测）；offline/ 和 agent/ 是可替换注入件。
不拆得更细——每个文件一个清晰职责，符合 KISS。

---

## 4. 核心设计决策（本设计的取舍与创新）

### 4.1 ★ 分层失败归因：规则快通道 + 反事实深归因

**问题**：归因方法在工程上分歧最大。纯规则快但浅，依赖 expected JSON 字段结构，难泛化到
自由文本 agent；全反事实严谨但慢，每 case 多次 evaluator 调用；LLM 裁判准但依赖 API，
违背「无 key 跑通」。

**本设计**：两级流水线，按 case 的信号明确度自适应。

```
每个失败 case
    │
    ▼
[第一层] 规则引擎（快，零成本）
   依据：EvalCaseResult 的 error_message / failed_metrics / actual-vs-expected tool 轨迹
   输出：(category, confidence)
   规则命中且 confidence=1.0  ──→ 直接采纳，结束
    │ (规则未命中 / 多规则冲突 / 信号弱)
    ▼
[第二层] 反事实干预（慢，有预算上限，默认 4 次/case）
   方法：深拷贝该 case 的 actual_conversation，逐变量替换重评：
     · 替换 final_response 为 expected  → metric 修复？→ final_response_mismatch
     · 替换 tool_name 为 expected       → metric 修复？→ tool_selection_error
     · 替换 tool_arguments 为 expected  → metric 修复？→ parameter_error
     · 规范化 format 重评               → metric 修复？→ format_violation
   单一修复命中 → 强归因（confidence 0.9）
   仅组合修复命中 → compound_failure（confidence 0.7）
   全未命中 → 按失败 metric 名兜底（confidence 0.5）→ 仍优于 UNKNOWN
```

**为什么是创新**：纯规则与全反事实的分层组合少见。全反事实把每个 case 都做多次干预（慢）；本设计只在规则
失效时才触发反事实，多数 case 走快通道，疑难 case 才付成本。**预算上限**保证总耗时可控（验收 ≤3 分钟）。

**fake/trace 模式下**：反事实重评用的也是 fake judge，零 API 成本，所以两层都便宜。
**online 模式下**：反事实有真实 judge 成本，预算上限（默认 4 次/case）保护开销。

**归因诚实性**：归因结果记录 `source`（rule/counterfactual/fallback）
和 `confidence`，report 里区分展示，不做「gold label 当预测」的自欺。

### 4.2 候选独立复评 + GEPA 提案捕获

**问题**：`AgentOptimizer.optimize()` 返回的 `best_pass_rate` 是优化器内部聚合分。优化器有动机高报
（它的目标就是提升分数）。直接信它 = 让运动员当裁判。

**本设计**：
1. `_ProposalCapture` 订阅 GEPA 的 `on_proposal_end` 回调，**捕获每一轮候选 prompt**，包括被 GEPA 内部
   丢弃的提案——这些往往是有教学价值的「优化后退化」样本。
2. 按完整 prompt 内容 sha256 去重。
3. 对每个去重候选，用 **trace-only `AgentEvaluator`** 在 train+val 上**独立重打分**，不读优化器的分。
4. Gate 只采信自己测出来的 train/val 证据。

这是防「优化器自吹」的最可信做法。代价是多花一次评测，但 fake/trace 模式下零成本。

### 4.3 过拟合三重检测

issue 硬验收点：「验证集退化但训练集提升」必须拒绝。单一公式会漏。本设计三道闸，任一触发即拒绝：

| 检测 | 公式 | 来源 |
|---|---|---|
| **显式过拟合** | `train_delta > 0 and val_delta <= 0` | 共识公式 |
| **泛化缺口** | `(train_score_delta - val_score_delta) > generalization_gap_threshold` | 抓「train 微升 val 平」的隐性感机 |
| **趋势背离** | train pass_rate 趋势升 且 val pass_rate 趋势降（多轮时） | 用 RoundRecord 序列 |

单轮优化时前两道生效；多轮优化时三道都生效。

### 4.4 prompt 敏感 FakeCallAgent（改 prompt 真改行为）

**问题**：静态 fixture 回放下，候选 prompt 改了但 call_agent 输出不变，优化是假象——验收
「三类场景」无法真实演示。

**本设计**：`FakeCallAgent` 每次调用**重读** `prompts/system.md`（prompt 热加载，与 advanced_strategies
一致），解析其中的**能力标记**决定输出。baseline prompt 故意缺能力，候选 prompt 补能力 → 行为真变。

```python
# 伪代码：能力标记协议（写在 prompt 里，对真实 LLM 是无害的自然语言注释）
# system.md (baseline): 故意不写 JSON 输出要求 → format 失败
# robust.md:        "Always respond as strict JSON with keys {route, answer}."  → format 修复
# ineffective.md:   与 baseline 相同                                     → 无提升
# overfit.md:       补了 train 需要的能力，但引入 val critical case 的错误分类 → val 退化
```

**注入路径**：通过 `AgentOptimizer.optimize(call_agent=...)` 和 `AgentEvaluator.get_executer(call_agent=...)`
的公开参数注入，不 monkeypatch SDK 内部，不依赖未公开 API。

### 4.5 PromptSandbox 候选隔离

**问题**：把候选 prompt 临时写进源文件再 finally 还原——中断/并发下有还原风险，可能污染 baseline。

**本设计**：候选 prompt 只写进**临时目录**的 `TargetPrompt`，进出 sandbox 各做一次 read-back 校验。
`update_source` 永远 `False`。Gate 通过后，可选的 `write_back_after_gate` 用 compare-and-swap 风格
三重摘要校验（写前验基线未变、写后验候选落地）才回写源文件。

### 4.6 审计三态成本 + fail-closed

**问题**：fake/trace 模式无真实 API 调用，成本是 0；但不能简单写 `cost=0`，否则和「online 模式真的花了 0 元」
无法区分，审计语义混乱。

**本设计**：`cost_measurement` 三态：
- `unavailable`：未知（online 模式但未拿到账单）→ **fail-closed，gate 判失败**
- `measured_zero_offline`：fake/trace，确定性的 0
- `measured_from_replay`：从 trace 的 token usage 重放估算

未知成本绝不误写成 0。这是审计严肃性的底线。

---

## 5. 各阶段详细设计

### 5.1 Baseline 评测

- 用 `AgentEvaluator.get_executer()` 对 train/val **分别**全量评测（不合并，便于后续按集做 delta）。
- `num_runs=1`（fake/trace 下确定性；online 可调高平滑方差）。
- 产出 `BaselineResult{train: SplitResult, val: SplitResult}`，每个 `SplitResult` 含
  `pass_rate / average_score / cases: list[CaseSnapshot]`。
- `CaseSnapshot` 是 `EvalCaseResult` 的归一化投影：`eval_id / passed / score / hard_fail /
  metrics[] / actual_response / expected_response / key_trajectory[]`。

### 5.2 失败归因

见 §4.1。输入 `CaseSnapshot`，输出 `FailureAttribution{category, confidence, evidence, source}`。
每个失败 case 至少一条可解释原因（issue 验收点 4）。report 聚合 `category_counts` 与 `coverage_rate`
（被归因的失败 case / 总失败 case）。

### 5.3 优化执行

- `AgentOptimizer.optimize()` 跑 GEPA，`_ProposalCapture` 捕获候选。
- fake 模式下：为确定性演示三类场景，`offline/candidates.py` 提供三个固定候选 prompt
  （`robust/ineffective/overfit`），通过 fixture optimizer 注入；同时保留真实 GEPA 路径（online）。
- 产出候选列表 `candidates: list[Candidate]`，每个含 `candidate_id / prompts / source(captured|fixture)`。
- **多字段 TargetPrompt 支持**：`TargetPrompt` 支持多次 `add_path` 注册多字段。本设计默认单字段
  `system_prompt`（满足 issue「system/skill/router 一种或多种」的下限），架构上可直接扩展到 `skill` /
  `router` prompt——多注册一个路径即可，归因/gate/report 全程按 `dict[str,str]` 处理，无需改逻辑。
- **每轮候选留痕**：`_ProposalCapture` 捕获的原始每轮提案（含被 GEPA 丢弃的）连同 `OptimizeResult.rounds`
  的 `RoundRecord`（`candidate_prompts / accepted / validation_pass_rate / round_llm_cost`）原样落盘到
  `audit/rounds.json`，满足 issue「保存每轮候选 prompt」的审计要求；report 里的 `candidates` 是去重后的视图。

### 5.4 候选独立复评 + 逐 case delta

- 每个候选在 PromptSandbox 内，用 trace-only `AgentEvaluator` 重测 train/val。
- `comparator.case_delta(baseline_case, candidate_case)` 按 `eval_id` join，输出 5 桶：
  `new_pass / new_fail / improved / regressed / unchanged`。
- 这一步直接服务 issue「区分新增通过、新增失败、分数提升、分数下降」要求。

### 5.5 Gate 决策

见 §8。三态：`accept / reject / needs_review`。`needs_review` 用于「提升不够 / 证据不足」等灰色地带
，不强行二分。每条规则记录 `passed / required / actual / expected / reason`。

### 5.6 审计落盘

每次运行落盘：
- `optimization_report.json`：主报告（schema 见 §6.4）
- `optimization_report.md`：人读版（基线表 / 归因表 / 候选决策表 / delta 表 / gate 表 / 复现命令）
- `audit/input.snapshot.json`：config + train/val/prompt 的 sha256 digest
- `audit/environment.snapshot.json`：sdk_version / git_commit / python / platform / mode / seed
- `audit/gate_decisions.json`：每候选的 gate 判定明细
- `audit/rounds.json`：GEPA 每轮原始 `RoundRecord`（候选 prompt、accepted、分数、单轮成本），未去重
- `audit/proposals.json`：`_ProposalCapture` 捕获的全部提案（含被 GEPA 内部丢弃的）
- 所有写盘用「写 .tmp + `os.replace`」原子写，`sort_keys=True` 保证确定性可 diff。

---

## 6. 数据结构

### 6.1 evalset.json（train/val 各 3 条）

沿用 SDK `EvalCase` schema（见 [_eval_case.py:170](../../../trpc_agent_sdk/evaluation/_eval_case.py#L170)）。
trace 模式 case 带 `eval_mode: "trace"` + `actual_conversation`。样例结构见
advanced_strategies/data/train.evalset.json。每条 case 在 `session_input.state` 里放归因所需的
`expected_trajectory` / `expected_format`（**仅 judge 可见，不进 prompt**，防答案泄漏）。

### 6.2 optimizer.json

```jsonc
{
  "evaluate": {
    "metrics": [
      { "metric_name": "final_response_avg_score", "threshold": 1.0, "criterion": {...} },
      { "metric_name": "tool_trajectory_avg_score", "threshold": 1.0, "criterion": {...} }
    ],
    "num_runs": 1
  },
  "optimize": {
    "eval_case_parallelism": 2,
    "stop": { "required_metrics": "all" },
    "algorithm": {
      "name": "gepa_reflective", "seed": 42,
      "reflection_lm": { "model_name": "${TRPC_AGENT_MODEL_NAME}", "api_key": "${TRPC_AGENT_API_KEY}" },
      "max_metric_calls": 60,
      "max_iterations_without_improvement": 4
    }
  }
}
```

### 6.3 gate.json（可配置阈值，全部外置不写死）

```jsonc
{
  "min_validation_score_delta": 0.05,      // val 提升下限（参数化，先验 0.01~0.1）
  "max_new_hard_fails": 0,                 // 禁止新增 hard fail
  "max_score_regression_per_case": 0.0,    // 单 case 退化上限
  "critical_case_ids": ["val_fiction_key"],  // 关键 case 不许退化
  "overfitting": {
    "enabled": true,
    "formula": "train_delta>0 and val_delta<=0",
    "generalization_gap_threshold": 0.1
  },
  "budget": {
    "max_metric_calls": 80,
    "max_duration_seconds": 180,           // 验收 ≤3 分钟
    "cost_measurement": "measured_zero_offline"
  },
  "tie_policy": "reject"                   // score 与 pass_rate 都≈0 → 拒绝
}
```

### 6.4 optimization_report.json schema

```jsonc
{
  "schema_version": "eval_optimize_loop.v1",
  "status": "accepted | rejected | needs_review | failed",
  "mode": "fake | trace | online",
  "seed": 42,
  "baseline": {
    "train":      { "split": "train",      "pass_rate": 0.0, "average_score": 0.5, "cases": [...] },
    "validation": { "split": "validation", "pass_rate": 0.33, "average_score": 0.6, "cases": [...] }
  },
  // 注：cases[] 每条含 case_id / passed / score / hard_fail / metrics[] /
  //     primary_failure(category) / failure_reasons[] / actual_response /
  //     expected_response / key_trajectory[]——即 issue 阶段 1 要求的
  //     「metric 分、pass/fail、失败原因、关键轨迹」全部 case 级落地。
  "candidates": [
    {
      "candidate_id": "robust",
      "source": "captured | fixture",
      "prompts": { "system_prompt": "..." },
      "train":    { "pass_rate": 1.0, "average_score": 1.0 },
      "validation": { "pass_rate": 1.0, "average_score": 1.0 },
      "delta": {
        "train":    { "pass_rate_delta": 1.0, "average_score_delta": 0.5 },
        "validation": { "pass_rate_delta": 0.67, "average_score_delta": 0.4 },
        "buckets": { "new_pass": [...], "new_fail": [...], "improved": [...], "regressed": [], "unchanged": [...] }
      },
      "gate": {
        "accepted": true, "overfitting_detected": false, "risk_level": "low",
        "checks": [ { "check": "validation_score_improved", "passed": true, "reason": "..." }, ... ]
      },
      "audit": { "prompt_sha256": "...", "optimizer_round": 3, "seed": 42 }
    }
  ],
  "selected_candidate_id": "robust",
  "failure_attribution": {
    "total_failed_cases": 4, "explained_failed_cases": 4, "coverage_rate": 1.0,
    "category_counts": { "format_violation": 2, "parameter_error": 1, "tool_selection_error": 1 },
    "by_case": { "train_hours_format": { "category": "format_violation", "confidence": 0.95, "source": "rule", "evidence": "..." } }
  },
  "optimizer": { "algorithm": "gepa_reflective", "status": "succeeded", "rounds": 4, "used_agent_optimizer": true },
  "data_quality": { "passed": true, "cross_split_duplicates": 0, "prompt_leakage_matches": 0 },
  "audit": {
    "run_id": "...", "started_at": "...", "finished_at": "...", "duration_seconds": 42.3,
    "seed": 42, "config_sha256": "...", "train_sha256": "...", "validation_sha256": "...",
    "baseline_prompt_sha256": { "system_prompt": "..." },
    "cost": {
      "measurement": "measured_zero_offline",  // unavailable | measured_zero_offline | measured_from_replay
      "optimization_usd": 0.0,                  // GEPA 反思 LM 花费（fake/trace 为 0）
      "evaluation_usd": 0.0,                    // 候选独立复评 + 反事实重评花费
      "total_usd": 0.0
    },
    "command": "python run_pipeline.py --mode fake"
  }
}
```

---

## 7. 失败归因 Taxonomy

8 类（覆盖 issue 列举的全部失败类型 + 兜底）：

| category | 触发信号 | 典型 layer |
|---|---|---|
| `format_violation` | JSON 解析失败 / 缺必需字段 / 格式正则不匹配 | 规则（快）|
| `tool_selection_error` | actual tool_names != expected tool_names | 规则（快）|
| `tool_parameter_error` | tool 名同但 args 异 | 规则（快）|
| `tool_call_error` | tool_response 含 error / failed status | 规则（快）|
| `final_response_mismatch` | final_response 与 expected 不符（且非上述）| 规则（快）/ 反事实 |
| `knowledge_recall_insufficient` | rubric metric 失败且涉及知识正确性 | 规则 / 反事实 |
| `llm_rubric_not_met` | 非 format 的 quality rubric 不达标 | 反事实 |
| `unknown` | 全部未命中（兜底，confidence 0.0）| fallback |

判定优先级（`missing` 必须在 `response` 前判定，否则误归）：
`tool_call > tool_selection > tool_parameter > format > knowledge_recall > llm_rubric > final_response > unknown`。

---

## 8. Gate 决策规则

AND 语义，每项独立判定。任一 required 失败 → reject。

| check | 规则 | 默认 |
|---|---|---|
| `evaluation_complete` | train/val 评测无缺失 | required |
| `validation_score_improved` | `val_score_delta >= min_validation_score_delta` | ≥0.05 |
| `validation_pass_rate_not_worse` | `val_pass_rate_delta >= 0` | required |
| `no_new_hard_fails` | `new_fail count <= max_new_hard_fails` | ≤0 |
| `no_case_regression` | 单 case score 退化 ≤ 阈值 | ≤0.0 |
| `no_critical_regression` | `critical_case_ids` 不在 regressed 桶 | required |
| `no_overfit` | §4.3 三重检测全过 | required |
| `budgets` | metric_calls / duration / cost 不超 | required |
| `tie_policy` | score 与 pass_rate 都≈0 时按策略 | reject |

`risk_level`：有 critical 回归或 overfit → high；其他失败 → medium；全过 → low。
决策：所有 required 通过 → `accept`；否则若仅是「提升不足/证据不足」类 → `needs_review`；安全类失败 → `reject`。

---

## 9. 三运行模式实现

| 关注点 | fake | trace | online |
|---|---|---|---|
| `call_agent` | `FakeCallAgent`（prompt 敏感）| 不调用 | 真实 `agent_module` |
| judge | `fake_rubric`（registry 注入）| `fake_rubric` | 真实 judge model |
| 候选来源 | fixture + GEPA 捕获 | trace 回放 | GEPA 捕获 |
| 成本 | `measured_zero_offline` | `measured_zero_offline` | `measured_from_replay`/`unavailable` |
| env 要求 | 无 | 无 | `TRPC_AGENT_API_KEY/BASE_URL/MODEL_NAME`（缺则 exit 2）|
| 用途 | 默认演示、验收 | CI 回归基线 | 真实业务 |

模式切换只在 `run_pipeline.py --mode` 一处，pipeline/ 内部模式无关。

---

## 10. 评测集设计（6 条 case，覆盖三类场景）

**业务域**：图书馆藏查询 agent（图书分类 fiction/science/history/faq + `search_catalog`/`check_availability` 工具查询 + JSON 输出）。
选这个域是因为它能自然同时触发 format / tool_parameter / knowledge / 分类错配 多类失败，且 critical case 设计直观。
**业务域兼顾多类失败的天然触发（format / tool / knowledge / 分类错配）。**

### 10.1 三类场景构造（核心：通过候选 prompt 的能力差异实现）

| 场景 | 候选 prompt 特征 | baseline→候选 预期 | gate 预期 |
|---|---|---|---|
| **可优化成功** (`robust`) | 补齐 JSON 格式 + 正确分类规则 + 工具查询 | train↑ val↑，critical 不退化 | accept |
| **优化无效** (`ineffective`) | 与 baseline 内容等价 | delta≈0 | reject（tie_policy）|
| **优化后退化** (`overfit`) | 补 train 能力，但引入「所有查询一律归 history」的过拟合规则 | train↑ val↓ | reject（overfit + critical）|

### 10.2 case 清单

**train（3 条，驱动优化）**
- `train_hours_format`：问开馆时间，baseline 返回纯文本非 JSON → `format_violation`
- `train_availability_args`：查《三体》可借状态，工具 `book_id` 参数错 → `tool_parameter_error`
- `train_author_lookup`：问《时间简史》作者，baseline 不调 `search_catalog` 直接猜 → `knowledge_recall_insufficient`

**val（3 条，驱动 gate，含 1 critical）**
- `val_fiction_key` **（critical）**：科幻小说查询，正确分类 `fiction`；overfit 候选会错归到 `history`
- `val_fiction_generalize`：另一科幻查询（泛化），overfit 同样错归 `history`
- `val_stable_membership`：办证政策 FAQ，全候选稳定（防「全失败」误导）

每条 case 的 expected（response 关键词 + 工具轨迹）只在 trace evalset 的 `conversation` 字段，
actual 由 `offline/fixtures.py` 按 variant 预录制；数据质量门 `data_quality` 检查 train/val 无 eval_id 重复。

---

## 11. 验收标准映射

| issue 验收点 | 落地位置 |
|---|---|
| 1. 6 条样例 case 全可运行 + 完整报告 | §10 case + `run_pipeline.py --mode fake` 产出 report |
| 2. 隐藏集决策准确率 ≥80% | §4.3 过拟合三重检测 + §8 gate 规则；tests 含隐藏样本断言 |
| 3. 「val 退化 train 提升」必拒绝 | §4.3 显式过拟合公式 + `no_critical_regression` |
| 4. 归因准确率 ≥75% + 每 case ≥1 可解释原因 | §4.1 分层归因 + `coverage_rate`；§4.1 记 source/confidence 防自欺 |
| 5. fake/trace 全流程 ≤3 分钟 | §3.2 三模式 + `budget.max_duration_seconds=180`；fake judge 零 API |
| 6. 报告含 baseline/candidate 分数、逐 case delta、gate 决策、理由 | §6.4 report schema |

---

## 12. 设计取舍与已知局限

**取舍**
- **归因分层 vs 全反事实**：选分层，牺牲少量归因严谨性换 ≤3 分钟时效。online 模式可调高反事实预算。
- **模块化 ~8 文件 vs 14 文件**：选中等。example 应可读，不应是生产框架。YAGNI——不预留未实现的扩展点。
- **call_agent 注入 vs ModelRegistry 注入**：选 `call_agent`（公开 API、零内部依赖）。ModelRegistry 路径
  虽更「真实跑全链路」，但依赖 SDK 未公开 API，作为 future enhancement 在 README 注明。
- **holdout 第三套集**：**不设**。6 条 case 已是 issue 下限，再切 holdout 会让每集太小失去统计意义。
  防过拟合交给 §4.3 三重检测 + 数据质量门。若后续扩到 20+ case，再加 holdout。

**已知局限**
- 6 条 case 规模小，过拟合检测是「构造性确定性」而非统计性。这是 issue 下限决定的，无法回避；
  设计上用三重检测 + 数据质量门最大化鲁棒性。
- fake judge 是规则 scorer，归因准确率上限受其真实性约束。online 模式换真实 judge 后归因更准。
- 反事实归因依赖 SDK metric 字段稳定性；SDK 字段变动需同步归因规则。

---

## 13. 实现里程碑

1. **M1 数据与骨架**：evalset(6) + system.md + optimizer.json + gate.json + models.py + config.py
2. **M2 fake 闭环**：FakeCallAgent + fake_rubric + 三候选 fixture → 跑通 baseline→report，产出 sample 报告
3. **M3 归因与 gate**：分层归因 + 逐 case delta + gate 三态 + 过拟合三重检测
4. **M4 审计**：PromptSandbox + 原子落盘 + sha256 + env snapshot
5. **M5 trace 模式 + 测试**：trace 回放 + pytest 断言三类场景 + 隐藏样本 + ≤3min
6. **M6 online 模式 + README**：真实 agent 接入 + 300-500 字方案说明

每个里程碑都有可运行产物，可独立 review。

---

## 附：设计要点回顾

本闭环的核心增量是 §4.1 的分层归因（规则快通道 + 反事实深归因自适应），用于同时满足
「归因准确率 ≥75%」与「≤3 分钟」两个看似冲突的验收点：多数 case 走规则快通道，
疑难 case 才触发反事实，且反事实用本地 metric 零成本。
