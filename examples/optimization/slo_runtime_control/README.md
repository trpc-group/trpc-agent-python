# SLO Runtime Control — 多重停止条件下的运行时 SLO 守门

> **适用场景**：在 CI 流水线 / 夜间窗口等具有硬性时间和资源约束的环境下运行 prompt 优化，需要"任何一个 SLO 触发都立刻停"的多重停止策略。本 example 演示同时启用 SDK 提供的全部 6 种 algorithm-level stop conditions，并通过任务设计让任一条件都有机会成为最先触发者。阅读前请先熟悉 `quickstart/README.md` §2。

## 1 · 适用问题与设计目标

线上业务跑 prompt 优化的现实约束：

- **时间预算硬性**：CI 流水线必须 N 分钟内结束，超出即失败
- **调用预算硬性**：LLM 后端配额按月计算，单次优化不能跑爆预算
- **候选池规模**：内存 / 评估时间随候选池增长，需要上限
- **早停灵敏度**：连续若干轮无改善应主动放弃，不耗费剩余预算

单一停止条件无法同时覆盖以上诉求。SDK 提供的 6 种 algorithm-level stop conditions 满足"OR 语义"——任意一条触发即停止，使业务可以叠加多重 SLO。

| 输入 | 输出 |
| --- | --- |
| 6 种 stop condition 的阈值组合 | 满足最先触发条件的最优候选 |
| `OptimizeResult.stop_reason` 字段 | 哪条 SLO 抢闸的明确反馈 |

### 本 example 演示的最小用例

| 维度 | 值 |
| --- | --- |
| 业务任务 | 客服工单分类（输入工单文本，输出 `{category, priority}` JSON） |
| 优化目标 | `agent/prompts/system.md` 单文件 |
| 验证指标 | `final_response_avg_score`（exact 匹配规范化 JSON） |
| 训练 / 验证规模 | 8 case / 4 case |
| 任务难度 | 训练集中混入 3 道边界混淆题，使 score_threshold 不会先抢闸，能观察其他 stopper 真实行为 |

## 2 · 术语对照

仅列出本 example 引入的新概念。基础术语见 `quickstart/README.md` §2。

| 术语 | 含义 |
| --- | --- |
| **algorithm-level stop condition** | GEPA 算法内部的停止判定（如预算、超时、无改善），写在 `optimizer.json` 的 `algorithm` 段。 |
| **framework-level metric stop** | 优化器框架基于 metric 阈值的早停判定，写在 `optimizer.json` 的 `optimize.stop` 段（如 `required_metrics`）。 |
| **OR 语义** | 多个 stop condition 同时启用时，**任意一条触发即停止**。这是本 example 的核心机制。 |
| **抢闸** | 在多 stop condition OR 语义下，最先满足条件的那条决定最终 `stop_reason`。 |
| **完成当前轮再停** | timeout 等条件触发后框架不会立即 kill 当前 round，而是等当前 round 完成（避免候选数据丢失/污染）。 |

## 3 · 运行示例

### 3.1 安装依赖

```bash
pip install -e ".[optimize]"
```

### 3.2 配置环境变量

```bash
export TRPC_AGENT_API_KEY="<your-key>"
export TRPC_AGENT_BASE_URL="<your-endpoint>"
export TRPC_AGENT_MODEL_NAME="<your-model>"
```

### 3.3 启动

```bash
python examples/optimization/slo_runtime_control/run_optimization.py
```

终端将输出每轮分数与最终 `stop_reason`，明确告知是哪条 SLO 触发了停止。

### 3.4 产物结构

```
runs/<timestamp>/
├── result.json           其中 stop_reason 字段标识抢闸者
├── summary.txt
├── baseline_prompts/
├── best_prompts/
└── rounds/
```

## 4 · 架构与数据流

```
optimizer.optimize()
    │
    ├─ baseline 评估
    │
    └─ for each round:
        ├─ GEPA 反思 → candidate prompt
        ├─ 写入 system.md
        ├─ EvalConfig 触发 call_agent for each train sample
        │   └─ create_agent() → Runner.run_async() → _normalize_response()
        │       ↓
        │   final_response_avg_score(text.match=exact)
        │
        └─ 6 个 stopper 在每轮结束时检查（OR 语义）：
              wall_clock          ≥ 90s ?
              metric_calls        ≥ 30 ?
              no_improvement      ≥ 3 轮 ?
              best_score          ≥ 1.0 ?
              proposals           ≥ 12 ?
              tracked_candidates  ≥ 5 ?
                       ↓
              任意一条满足 → 立即收尾，stop_reason 写入 OptimizeResult
```

### 4.1 文件清单

| 文件 | 角色 | 接入自有业务时的修改方向 |
| --- | --- | --- |
| `run_optimization.py` | 优化器入口（含 `_normalize_response`） | 与 quickstart 同 |
| `agent/agent.py` | LlmAgent 工厂 | 替换为业务 agent |
| `agent/prompts/system.md` | baseline prompt | 写入业务 baseline |
| `optimizer.json` | **核心改造点**：6 stop condition 阈值组合 | 按业务 SLO 调整每条阈值 |
| `train.evalset.json` / `val.evalset.json` | 数据集 | 替换为业务用例 |

## 5 · 6 种 stop condition 详解

| 字段 | 本 example 值 | gepa 内部映射 | 抢闸条件 | 适用场景 |
| --- | --- | --- | --- | --- |
| `max_metric_calls` | 30 | `MaxMetricCallsStopper` | 累计 case 评估次数 ≥ 30 | LLM 配额硬上限 |
| `max_iterations_without_improvement` | 3 | `NoImprovementStopper` | 连续 N 轮 best valset 无提升 | 优化已收敛或陷入局部最优时主动放弃 |
| `timeout_seconds` | 90.0 | `TimeoutStopCondition` | wall-clock ≥ N 秒 | CI 流水线时间窗硬约束 |
| `score_threshold` | 1.0 | `ScoreThresholdStopper` | best valset pass_rate ≥ 阈值 | 已达业务目标，无需继续 |
| `max_candidate_proposals` | 12 | `MaxCandidateProposalsStopper` | reflection LM 累计提议次数 ≥ N | 限制反思 LM 调用预算 |
| `max_tracked_candidates` | 5 | `MaxTrackedCandidatesStopper` | Pareto 前沿候选池大小 ≥ N | 控制内存与 merge 候选空间规模 |

### 5.1 至少配 1 个

`optimizer.json` 中至少配置上述 6 个字段中的 1 个，否则框架启动期 `_require_at_least_one_stop_condition` 报错。**多个同时启用即 OR 语义**——任一触发立即停止。

### 5.2 显式禁用 framework-level metric 早停

```jsonc
{
  "optimize": {
    "stop": {
      "required_metrics": []                  // 显式禁用框架层 metric 早停
    },
    "algorithm": {
      "max_metric_calls": 30,
      "max_iterations_without_improvement": 3,
      "timeout_seconds": 90.0,
      "score_threshold": 1.0,
      "max_candidate_proposals": 12,
      "max_tracked_candidates": 5
    }
  }
}
```

`required_metrics: []` 让 6 个 algorithm 级 stopper 独占 stop 决策权——避免框架层在 algorithm 层之前提前终止，影响对底层 stopper 行为的观察。

业务真实使用时是否禁用 framework-level 早停取决于诉求：

- 仅关心 algorithm-level 时序与开销控制 → 禁用（本 example 的选择）
- 同时关心 metric 是否达标 → 启用 `required_metrics: "all"` 或具体 metric 列表（参见 quickstart §5）

## 6 · 关键配置

### 6.1 timeout 不是 hard kill

`timeout_seconds=90` 触发后框架不会立即 kill 当前正在跑的 round，而是等当前 round 结束。实际终止时间通常超过设定值。原因：中途 kill 会导致候选数据丢失 / 文件写入截断。

**业务面应对**：

- 若 SLO 是**硬截止**（如 CI 流水线必须 N 分钟内结束），把 `timeout_seconds` 设为真实窗口的一半左右留出缓冲
- 单轮典型耗时由 LLM 调用速度决定。可通过缩小 `reflection_minibatch_size` / `eval_case_parallelism` 控制单轮时长

### 6.2 阈值之间的相对关系

阈值之间应保持自洽，否则部分 stopper 永远不会触发：

| 关系 | 含义 |
| --- | --- |
| `max_metric_calls > reflection_minibatch_size × max_iterations_without_improvement` | 否则 no_improvement 永远先抢闸 |
| `timeout_seconds > 单轮典型耗时 × 2` | 否则 timeout 在第 1 轮就触发，看不到优化进展 |
| `max_candidate_proposals ≥ 1` | 至少要让 reflection LM 跑过一次 |
| `max_tracked_candidates ≥ 2` | 否则 Pareto 前沿无法保留多于 baseline 的候选 |

### 6.3 `_normalize_response` 的复用

与 `blackbox_cli/` example 完全相同的规范化逻辑：用 `json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"))` 把 LLM 输出转换为唯一字符串形态，使 `final_response_avg_score(text.match=exact)` 可直接走精确匹配，**评测层不依赖 LLM judge**——这对运行时控制场景至关重要，避免 judge 调用引入额外不确定性与时间开销。

## 7 · 常见问题

**Q：`stop_reason` 字段值有哪些？**
A：常见取值包括 `score_threshold_reached` / `budget_exhausted` / `timeout_reached` / `no_improvement` / `max_proposals_reached` / `max_tracked_candidates_reached` / `user_requested_stop`（由 `optimize.stop` 文件触发）。具体取值由触发的 stopper 决定。

**Q：触发 timeout 后产物完整吗？**
A：完整。"完成当前轮再停"语义保证当前轮的 round_*.json、result.json、summary.txt 都已写入。中途中止仅丢弃尚未开始的下一轮。

**Q：能否调整 stop condition 的优先级？**
A：不能。多 stop condition 间是 OR 语义且同步检查，最先满足条件的 stopper 决定 `stop_reason`。需要"优先看 timeout，timeout 之内尽量跑高 score"这种语义时，应把次要 stopper 的阈值放宽到永远不会先触发。

**Q：单轮已经超过 timeout 了怎么办？**
A：仍会等当前轮跑完才停止。若该轮跑得太久（如 LLM 卡住），可在 `call_agent` 内部对 LLM 调用加超时（见 `blackbox_cli/agent/call_agent.py` 的 `CLI_TIMEOUT_SEC` 模式）。

**Q：业务里只关心 timeout，其他不限怎么配？**
A：仅设 `timeout_seconds=<秒数>`，其余 5 个字段不写即可（默认禁用）。但需注意至少配 1 个 stopper。

## 8 · 实验建议：让其他 stopper 抢闸

通过调整阈值组合可以观察不同 stopper 的真实行为。可作为业务调参参考：

| 想看哪条 stopper 抢闸 | 阈值调整方向 |
| --- | --- |
| `score_threshold` | 把 baseline 写得"约束更紧"让 GEPA 容易达 1.0；或把 score_threshold 调到 0.7 |
| `max_metric_calls` | 把 timeout_seconds 调高（如 600）+ minibatch 调小让评估速度快 |
| `max_iterations_without_improvement` | timeout_seconds 调高 + 任务设计成"难以再提升"的边界场景 |
| `max_candidate_proposals` | 调到 2、timeout=300 |
| `max_tracked_candidates` | 调到 2、timeout=300、`frontier_type="hybrid"`（多候选并存） |

业务真实接入步骤：

1. 测量典型业务负载下单轮耗时与单轮 metric_calls 数
2. 按 SLO 反推每个 stopper 的合理阈值（如 CI 5min → timeout=180s 留 60s 缓冲）
3. 跑一次基准实验观察 `stop_reason` 是否如期
4. 根据实际行为微调阈值

> 业务真实接入时不要复制本 example 的 6 个值——本 example 的阈值是为"演示效果可见"而设，实际业务应根据 LLM 后端速度、数据集规模、SLO 窗口反推。
