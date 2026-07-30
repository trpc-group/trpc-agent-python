# Evaluation + Optimization Pipeline — E-Commerce Shopping Assistant

完整的「评测 → 失败归因 → Prompt 优化 → 回归验证 → 产物审计」自动闭环流水线，场景为电商购物助手。

> **本文档同时是本示例的设计说明。** 它描述的是**目标状态**；实现进行中时，部分内容会先于代码存在。
> 前置阅读：[Prompt 自优化（AgentOptimizer）](../../../docs/mkdocs/zh/optimization.md)。

---

## 1 这个示例要证明什么

一条 prompt 优化闭环能不能进业务关键链路，取决于三件事：

1. **优化器改的东西真的生效了吗** —— 候选 prompt 写盘后，下一次推理是否读到了它
2. **验证的是候选还是基线** —— 门控打的分，来自新 prompt 还是旧 prompt
3. **门控敢不敢拒绝** —— 训练集涨分但验证集退化时，能不能拦住

本示例用一个刻意构造的数据集回答这三问：验证集里既有「优化能修好」的 case，也有「优化修不好」的 case，还有一条 **`val_003_regression`——优化后反而退化**。门控必须拒绝这个候选。如果流水线在任何一环偷懒（比如复用预录制数据、或候选没真正生效），这条 case 就拦不住，而整条闭环的可信度也就没了。

---

## 2 三条推理路径（理解本示例的关键）

SDK 提供三种「拿到 agent 实际输出」的方式，本流水线三种都用到了。**它们的能力不同，这个差异决定了整个架构**：

| 路径 | 谁在用 | SDK 后端 | 需要 API key | 能否拿工具轨迹 |
|---|---|---|---|---|
| trace（预录制） | demo 的 Stage 1/4 | 无 | 否 | 能（从预录制轨迹读） |
| `runner=` | real 的 Stage 1/4 | `LocalEvalService` | 是 | **能** |
| `call_agent=` | Stage 3 优化器 | `RemoteEvalService` | 是 | **不能** |

第三行是 SDK 的硬约束，不是本示例的选择：`AgentOptimizer.optimize` 只接受 `call_agent`（源码 `_agent_optimizer.py`），而 `call_agent` 的签名是 `async (str) -> str`——它只能返回一段文本，拿不到 session traces 和 tool 调用记录。

由此产生两条**必须遵守**的推论：

**推论一：优化器用不了 `tool_trajectory_avg_score`。** SDK 会在启动期 fail-fast 拒绝——见 `_agent_optimizer.py` 的 `_DISALLOWED_METRICS_IN_CALL_AGENT_MODE`，该集合同时包含 `llm_rubric_knowledge_recall`（它需要 `Invocation.intermediate_data`，而 `RemoteEvalService` 留空）。所以门控能守的指标，比优化器能优化的指标**更多**——这不是我们想让门控更严，是优化器够不着。

**推论二：优化器用不了 `eval_mode: "trace"` 的数据集。** `RemoteEvalService._reject_trace_cases` 会抛 `ValueError: call_agent mode is incompatible with trace cases`。所以 real 模式必须有一套独立的 non-trace 数据集。

---

## 3 架构

### 3.1 评测后端抽象

`pipeline/_eval_backend.py` 是唯一封装「agent 的实际输出从哪来」的地方：

```
EvalBackend (Protocol)
  async evaluate(eval_set_path, metrics_config_path, num_runs)
      -> (EvaluateResult, EvalSetReport)

  ├─ TraceBackend    demo：不传 runner/call_agent → trace 模式，零 LLM 调用
  └─ LiveBackend     real：持 agent 工厂，每次 evaluate() 现建 Runner
```

**`LiveBackend` 持的是工厂而非实例**，这是刻意的。Stage 1 和 Stage 4 之间 prompt 文件会被改写；工厂保证每次评测都重新 `create_agent()`、重读 `system.md`。如果持实例，`LlmAgent` 的 `instruction` 在构造时就固化了，Stage 4 会拿旧 prompt 打分——而这正是本示例要防的第 1、2 类失败。

### 3.2 编排层

`PipelineRunner` 收一个 `EvalBackend` 而非 `demo_mode: bool`。六个阶段的代码在两种模式下**逐字相同**，`demo_mode` 只在 `run_pipeline.py` 组装 backend 时出现一次。

这么设计是因为：demo 和 real 的真正差异**只有一处**——实际输出从哪来。把它收敛到一个点，其余五个阶段的行为在两种模式下天然一致，这本身就是对「两种模式结论可比」的结构性保证。

### 3.3 Stage 4 必须显式写回候选

一个容易漏掉的 SDK 契约：`update_source=False` 时，优化器在 `finally` 阶段会把源 prompt 文件**回滚成 baseline**（见优化文档 FAQ 末条）。所以 `optimize()` 返回后，`agent/prompts/system.md` 里躺的是 baseline，**不是最优候选**。

Stage 4 要验证候选，必须自己写回去：

```python
async with applied_prompts(target_prompt, best_prompts):   # write_all(best)
    candidate_report = await backend.evaluate(val_path, ...)
# finally: write_all(baseline) —— 无论评测是否抛异常都还原
```

复用 SDK 的 `TargetPrompt.write_all`，直接继承它的原子写 + 多字段失败回滚保证。demo 模式下该上下文退化为 `contextlib.nullcontext()`，不在 `run()` 里长出模式分支。

---

## 4 目录结构

```
eval_optimize_loop/
├── run_pipeline.py                    # CLI 入口；唯一出现 demo_mode 分支的地方
├── pipeline/
│   ├── _models.py                     # Pydantic 数据模型
│   ├── _eval_backend.py               # TraceBackend / LiveBackend
│   ├── _runner.py                     # PipelineRunner 编排器
│   ├── _stage_baseline.py             # Stage 1
│   ├── _stage_failure_attribution.py  # Stage 2
│   ├── _stage_optimization.py         # Stage 3
│   ├── _stage_validation.py           # Stage 4
│   ├── _stage_acceptance_gate.py      # Stage 5
│   └── _stage_audit_trail.py          # Stage 6
├── data/
│   ├── trace/                         # demo：eval_mode=trace，含 actual_conversation
│   │   ├── train.evalset.json
│   │   ├── val_baseline.evalset.json
│   │   └── val_optimized.evalset.json    # demo 专用：模拟"优化后"表现
│   ├── live/                          # real：无 actual_conversation
│   │   ├── train.evalset.json
│   │   └── val.evalset.json
│   ├── gate_metrics.json              # 2 metric（门控全集）
│   ├── optimizer.json                 # 优化器配置（response 级 metric 子集）
│   ├── demo_optimize_result.json      # demo 用的预生成 OptimizeResult
│   └── _generate_evalsets.py          # 一次生成上述两套数据集
├── agent/
│   ├── agent.py                       # 购物助手 Agent + 工具注册
│   ├── config.py
│   ├── tools.py
│   └── prompts/system.md              # 优化目标
├── tests/
└── output/
```

`gate_metrics.json` 这个名字是有意的：两份 metric 配置并存时，原来的 `test_config.json` 看不出它是**更严的那一份**。

---

## 5 数据集

两套并存，由 `data/_generate_evalsets.py` 一次生成、全部入库。

| 数据集 | eval_mode | 用途 | 内容 |
|---|---|---|---|
| `trace/train.evalset.json` | trace | demo Stage 1 | 期望 + 预录制实际输出 |
| `trace/val_baseline.evalset.json` | trace | demo Stage 1 | 同上 |
| `trace/val_optimized.evalset.json` | trace | demo Stage 4 | 模拟优化后的实际输出 |
| `live/train.evalset.json` | 非 trace | real Stage 3（优化器训练集） | 只有期望 + session_input |
| `live/val.evalset.json` | 非 trace | real Stage 1/3/4 | 同上 |

`live/` 两个文件路径必须不同——`AgentOptimizer` 会规范化路径后比对，相同则抛 `ValueError` 防数据泄漏。

### Case 设计

20 训练 + 20 验证，四类场景，由 eval_id 后缀标识：

| 后缀 | 场景 | 含义 |
|---|---|---|
| `_optimizable` | `optimizable_success` | 改 prompt 能修好（如回复过短） |
| `_ineffective` | `optimization_ineffective` | 改 prompt 修不好（如底层幻觉） |
| `_working` | `optimization_regression` | 基线已通过，不能被改坏 |
| `_regression` | `optimization_regression` | demo 中优化后退化，用于验证门控会拒绝 |

**场景类型按后缀推导，不维护映射表。** 硬编码的 `SCENARIO_MAP` 和 evalset 是两个独立事实来源，必然漂移。

---

## 6 流水线阶段

| 阶段 | 说明 |
|---|---|
| 1. 基线评测 | 经 backend 对训练集和验证集打分 |
| 2. 失败归因 | 按 `final_response_mismatch` / `tool_trajectory_mismatch` 等聚类 |
| 3. 优化执行 | real 跑 `AgentOptimizer`；demo 加载预生成结果 |
| 4. 候选验证 | 写回 `best_prompts` 后重评验证集，与基线逐 case 算 delta |
| 5. 接受门控 | 多检查 AND：提升阈值、无新增 hard failure、回归上限、关键 case、成本预算 |
| 6. 审计落盘 | `optimization_report.json` + `.md` |

### Stage 1：异常处理的边界

`AgentEvaluator` 在分数低于 threshold 时抛 `AssertionError`——而**基线本来就该有失败 case**，这是流水线的输入前提，不是错误。所以只吞这一类：

```python
try:
    await executer.evaluate()
except AssertionError:
    pass          # 预期：baseline 达不到 threshold，正是待优化的输入
# 其他异常（文件缺失、配置错误、模型调用失败）继续上抛
```

宽泛的 `except Exception: pass` 会把「数据集文件缺失」静默成全 0 分，然后 Stage 2 把它归因成 prompt 问题——这是最坏的失败模式：流水线看起来跑通了，结论全是错的。

### Stage 3：`call_agent` 必须每次重建 agent

```python
async def call_agent(query: str) -> str:
    agent = create_agent(demo_mode=False)    # 每次重建 → 重读 system.md
    ...
```

优化文档 §4.1/§4.2/§4.3 的接入自检表第一行都是这条。agent 在闭包外只建一次的话，GEPA 每轮写入的候选永不生效，所有轮次都在评估同一份 baseline——表现为 `accepted` 全 false、`mergeRoundsTotal=0`，而且**不会报任何错**。

### Stage 4：两种模式下「候选」的含义不同

demo 里候选是**另一个 evalset 文件**（`val_optimized`）；real 里候选是**同一个验证集配上新 prompt**。编排层接 `val_baseline_path` 和 `val_candidate_path` 两个参数——demo 传不同文件，real 传同一个文件，差异在组装时解决。

---

## 7 两层 Metric 策略

| | 配置 | metric | 用途 |
|---|---|---|---|
| **门控**（Stage 1/4） | `gate_metrics.json` | `final_response_avg_score` + `tool_trajectory_avg_score` | 守全集 |
| **优化器**（Stage 3） | `optimizer.json` 的 `evaluate` 段 | `final_response_avg_score` | 优化子集 |

门控严于优化器，**源于 §2 推论一的 SDK 约束**，不是设计偏好。好处是工具调用层面的回归依然能被拦住——即使优化器看不见这个维度。

`num_runs` 也不对称：门控侧 2（消除 LLM 输出方差，避免污染 delta），优化器侧 1（调 2 会让 metric_calls 翻倍撞预算）。**门控要稳，优化器要省。**

---

## 8 优化器配置

`data/optimizer.json` 关键字段：

| 字段 | 值 | 推导 |
|---|---|---|
| `max_metric_calls` | 150 | baseline 20 + 5 轮×6 + 2 次 valset 重评×20 ≈ 90，留 1.6× 余量 |
| `timeout_seconds` | 1800 | 文档 §4.7 要求多重 stopper（OR 语义） |
| `max_iterations_without_improvement` | 5 | 连续 5 轮无提升即放弃 |
| `candidate_selection_strategy` | `pareto` | 文档默认推荐 |
| `skip_perfect_score` | `true` | 20 条 train 中已有满分 case，省反思预算 |
| `eval_case_parallelism` | 2 | 控制瞬时 QPS |

> **这些是初值，不是定论。** 文档 §6.4 的做法是先测再定：按当前配置跑一次基准，从 `output/<ts>/optimizer/result.json` 读 `rounds[*].durationSeconds` 中位数和 `totalMetricCalls / totalRounds`，再按自己的 SLO 反推。
>
> 尤其注意 `max_metric_calls` 与验证集规模的关系：验证集 20 条时，**一次 baseline 评估就吃掉 20 次预算**。配得太小会导致 budget 永远先抢闸，其他 stopper 形同虚设（`stop_reason` 恒为 `budget_exhausted`）。

---

## 9 产物

```
output/<timestamp>/
├── optimizer/                  # AgentOptimizer 的 output_dir
│   ├── result.json             # 完整 OptimizeResult（字段名 camelCase）
│   ├── summary.txt             # 人类可读摘要
│   ├── run.log                 # 单行状态，CI grep 友好
│   ├── config.snapshot.json    # 配置快照，可复跑
│   ├── rounds/round_NNN.json   # 每轮 RoundRecord
│   ├── baseline_prompts/       # 运行前快照
│   └── best_prompts/           # 最优候选
├── optimization_report.json    # 流水线报告（机器可解析）
└── optimization_report.md      # 流水线报告（人类可读）
```

时间戳子目录避免重跑覆盖上次审计（优化文档 FAQ 明确要求每次跑用独立子目录）；`optimizer/` 子目录让 SDK 产物与流水线报告不再混在一起。

**运行中想优雅停机**：`touch output/<ts>/optimizer/optimize.stop`，下一轮开头即停，`stop_reason="user_requested_stop"`。比 Ctrl+C 干净，能保证当前轮完成后正常落盘。

**流水线报告消费的优化器审计字段**：`stop_reason`、`finish_reason`、`total_token_usage`、`total_metric_calls`，以及逐轮的 `accepted` / `acceptance_reason` / `validation_pass_rate` / `failed_case_ids` / `optimized_field_names`。

---

## 10 快速开始

```bash
cd examples/optimization/eval_optimize_loop

# Demo 模式（无需 API key，使用预录制 trace 数据）
python run_pipeline.py

# Real 模式（需要 API key）
export TRPC_AGENT_API_KEY=<your-key>
export TRPC_AGENT_BASE_URL=<your-endpoint>
export TRPC_AGENT_MODEL_NAME=gpt-4o-mini      # 可选
python run_pipeline.py --no-demo-mode

# 查看报告
cat output/<timestamp>/optimization_report.md
```

### 命令行选项

```
--output-dir DIR          输出根目录（默认 output/）；每次运行在其下建 <timestamp>/ 子目录
--no-demo-mode            进入 real 模式（需要 API key）
--min-improvement F       接受候选所需的最小 pass rate 提升（默认 0.0）
--max-regressions N       允许的最大回归 case 数（默认 0）
--allow-regressions       允许任意回归
--critical-cases ID...    必须通过的关键 case ID
--max-cost F              最大 LLM 成本预算 USD（默认 0 = 不限制）
```

### 重新生成数据集

```bash
python data/_generate_evalsets.py     # 一次生成 trace/ 和 live/ 两套
```

---

## 11 Agent 工具

| 工具 | 说明 |
|---|---|
| `search_products(query, category)` | 搜索商品，支持按分类过滤 |
| `get_product_details(product_id)` | 获取商品详情（价格、评分、库存等） |
| `add_to_cart(product_id, quantity)` | 添加商品到购物车 |
| `check_order_status(order_id)` | 查询订单状态 |
| `get_cart()` | 查看购物车内容 |
| `apply_coupon(code)` | 应用优惠券 |

---

## 12 测试

```bash
pytest examples/optimization/eval_optimize_loop/tests/ -v
```

**real 路径的接线无需 API key 即可测试**——`LiveBackend` 持的是工厂，注入 stub agent 工厂即可覆盖。

回归守门（对应本次修复的四个 P0，防止它们悄悄回来）：

| 测试 | 守住什么 |
|---|---|
| `live/` 数据集不含 trace case | 优化器收到 trace 数据会抛 `ValueError` |
| `call_agent` 每次调用都重建 agent（计数器断言） | 候选 prompt 不生效 |
| `applied_prompts` 在评测抛异常时仍还原 baseline | 源 prompt 被污染 |
| 数据集文件齐全 | Stage 4 因文件缺失崩溃 |

一致性守门：

| 测试 | 守住什么 |
|---|---|
| `gate_metrics.json` ⊇ `optimizer.json` 的 metric | 两层策略的包含关系 |
| `optimizer.json` 不含黑盒不兼容 metric | 启动期 fail-fast（比对 `_DISALLOWED_METRICS_IN_CALL_AGENT_MODE`） |
| scenario 按 eval_id 后缀推导正确 | 场景标注 |

---

## 13 已知失败模式

这条闭环有几个**不会报错但结论全错**的失败模式，实现和 review 时重点检查：

| 症状 | 根因 | 怎么发现 |
|---|---|---|
| `accepted` 全 false、`mergeRoundsTotal=0` | 候选 prompt 没生效（agent 复用实例） | 断言 agent 构造次数 |
| 门控结论与实际 agent 行为不符 | Stage 4 评的是预录制数据而非候选 | 检查 Stage 4 是否写回 `best_prompts` |
| `stop_reason` 恒为 `budget_exhausted` | `max_metric_calls` 小于验证集规模的数倍 | 读 `result.json` 反推 |
| 归因报告显示大量 `final_response_mismatch` 但 prompt 看起来没问题 | 评测异常被静默吞掉 | 收窄 `except` 范围 |
