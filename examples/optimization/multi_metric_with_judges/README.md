# Multi-Metric with Judges — 多 metric 与 multi-judge 集成

> **适用场景**：业务 agent 同时受多类约束（答案正确性硬约束 + 风格 / 安全 / 合规软约束），需要多条 metric 共同参与优化与早停判定，并希望通过多 judge 投票降低单 LLM 裁判的偏差。本 example 演示 `llm_final_response`（多 judge 投票）+ `llm_rubric_response`（单 judge 多 rubric）双 metric 共存、`frontier_type="hybrid"` 双层 Pareto 前沿、`stop.required_metrics` 显式列表的完整配置。阅读前请先熟悉 `quickstart/README.md` §2。

## 1 · 适用问题与设计目标

单 metric 优化在工程实践中往往不够：

- "答案对就行"忽视格式 / 风格 / 合规约束，容易拿到"对但不能用"的回答
- 单 LLM 裁判存在偏差（temperature 抖动、prompt 暗示、模型偏好），尤其在主观维度上
- 不同 metric 反映不同业务诉求，应能在前沿上协同存在而非互相覆盖

本 example 的设计原则：

- **硬约束 / 软约束分离**：`llm_final_response` 用 multi-judge `all_pass` 投票把关答案正确性；`llm_rubric_response` 用单 judge 多 rubric 评估格式 / 风格
- **多 judge 投票降低偏差**：3 个 judge 在不同 temperature 下独立判断，全体通过才算 PASS
- **双层 Pareto 前沿**：`frontier_type="hybrid"` 同时维护 per-case 与 per-metric 前沿，避免"为了改 metric A 牺牲 metric B"的退化
- **稳定评估**：`num_runs=2` 平滑 LLM 输出方差；`eval_case_parallelism=1` 控制 multi-judge 并发避免 rate limit

| 输入 | 输出 |
| --- | --- |
| 多条 metric（每条独立 threshold + 独立判分逻辑） | 同时满足所有指定 metric 阈值的最优候选 |
| `stop.required_metrics` 中列出的"必须达标"的 metric 子集 | 严格的早停判定：列表中所有 metric 在 val 集上达标才提前终止 |

### 本 example 演示的最小用例

| 维度 | 值 |
| --- | --- |
| 业务任务 | 数学辅导 agent，要求答案正确 + 风格规范（无 emoji、推理清晰、答案带单位） |
| 优化目标 | `agent/prompts/system.md` 单文件 |
| 验证指标 | `llm_final_response`（3 judge `all_pass`，threshold 1.0） + `llm_rubric_response`（4 rubric 均值，threshold 0.75） |
| 训练 / 验证规模 | 5 条 / 3 条 |

## 2 · 术语对照

仅列出本 example 引入的新概念。基础术语见 `quickstart/README.md` §2。

| 术语 | 含义 |
| --- | --- |
| **`llm_final_response` metric** | 由 LLM 裁判判断 agent 输出是否与参考答案实质一致，输出 PASS / FAIL。可配置多个 judge 共同打分。 |
| **`llm_rubric_response` metric** | 单 LLM 裁判按多条 rubric（评分标准）独立打分后取均值；适合多维度软约束。 |
| **multi-judge** | `criterion.llm_judge.judge_models` 数组形式配置多个独立 judge，每个 judge 独立调用 LLM 给出判断。 |
| **`models_aggregator`** | 多 judge 结果的聚合策略，6 种取值（见 §5.2）。本 example 用 `all_pass`。 |
| **frontier_type** | Pareto 前沿的粒度。4 种取值：`instance`（按 case） / `objective`（按 metric） / `hybrid`（双层） / `cartesian`（按 case×metric）。多 metric 推荐 `hybrid`。 |
| **stop.required_metrics** | 框架层早停的 metric 子集声明。`"all"` / 列表 / `null` 三种形式。 |

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

agent、reflection LM、所有 judge 默认共用同一组凭据。需要让 judge 用独立模型时单独配置 `judge_model` 字段。

### 3.3 启动

```bash
python examples/optimization/multi_metric_with_judges/run_optimization.py
```

单次运行约 5–10 分钟。每条 case 一次评测约触发 (3 + 1) × 2 = 8 次 LLM 调用（3 个 judge × `num_runs=2` 加 1 个 rubric judge × `num_runs=2`）。

### 3.4 产物结构

与 quickstart 一致。`result.json` 中 `metric_breakdown` 字段会同时包含 `llm_final_response` 与 `llm_rubric_response` 两条独立分数。

## 4 · 架构与数据流

```
每个 case 一次评测：
├── agent 输出 final_text
│
├── llm_final_response  (硬约束)
│      ├─ judge_1 (temperature=0.0) → valid / invalid
│      ├─ judge_2 (temperature=0.3) → valid / invalid
│      ├─ judge_3 (temperature=0.6) → valid / invalid
│      └─ aggregator: all_pass → 三个全 valid 才算 PASS（threshold=1.0）
│
└── llm_rubric_response  (软约束，单 judge 多 rubric)
       ├─ rubric: no_emoji_or_slang   → 0/1
       ├─ rubric: numeric_correct      → 0/1
       ├─ rubric: reasoning_clear      → 0/1
       └─ rubric: units_present        → 0/1
       平均分 = quality score（threshold=0.75 ≈ 4 条至少 3 条过）

stop.required_metrics = ["llm_final_response", "llm_rubric_response"]
       两个 metric 都在 val 集上达 threshold 才提前停止
```

### 4.1 文件清单

| 文件 | 角色 | 接入自有业务时的修改方向 |
| --- | --- | --- |
| `run_optimization.py` | 优化器入口 | 与 quickstart 同；多 metric 场景下基本不变 |
| `agent/agent.py` | LlmAgent 工厂 | 替换为业务 agent 构建逻辑 |
| `agent/prompts/system.md` | baseline prompt | 写入业务 baseline |
| `optimizer.json` | **核心改造点**：多 metric / multi-judge / hybrid frontier 配置 | 按业务 metric 数量与维度调整 |
| `train.evalset.json` / `val.evalset.json` | 数据集 | 替换为业务用例（reference 字段需配合 metric 类型） |

## 5 · 关键配置

### 5.1 多 metric 与 multi-judge 配置示例

```jsonc
{
  "evaluate": {
    "num_runs": 2,                     // 平滑 LLM 输出方差
    "metrics": [
      {
        "metric_name": "llm_final_response",
        "threshold": 1.0,
        "criterion": {
          "llm_final_response": {
            "llm_judge": {
              "judge_models": [        // 多 judge 数组：3 个独立 judge
                { "...": "..." , "generation_config": { "temperature": 0.0 } },
                { "...": "..." , "generation_config": { "temperature": 0.3 } },
                { "...": "..." , "generation_config": { "temperature": 0.6 } }
              ],
              "models_aggregator": "all_pass"   // 全 PASS 才算通过
            }
          }
        }
      },
      {
        "metric_name": "llm_rubric_response",
        "threshold": 0.75,
        "criterion": {
          "llm_rubric_response": {
            "llm_judge": { "judge_model": { "...": "..." } },
            "rubrics": [
              { "name": "no_emoji_or_slang", "description": "..." },
              { "name": "numeric_correct",   "description": "..." },
              { "name": "reasoning_clear",   "description": "..." },
              { "name": "units_present",     "description": "..." }
            ]
          }
        }
      }
    ]
  },
  "optimize": {
    "eval_case_parallelism": 1,
    "stop": {
      "required_metrics": ["llm_final_response", "llm_rubric_response"]
    },
    "algorithm": {
      "frontier_type": "hybrid",
      "max_metric_calls": 30
    }
  }
}
```

### 5.2 `models_aggregator` 6 种取值

按业务严格度从严到松排序：

| aggregator | 通过条件 | 适用场景 |
| --- | --- | --- |
| `all_pass` | 全部 judge 判 PASS | 合规 / 安全场景，任何一票否决都拦截（最严格） |
| `weighted_majority` | 加权 PASS 票 > FAIL 票 | 不同 judge 信任度不同（如主 judge 权重 2、副 judge 权重 1） |
| `majority_pass` | 超过半数 judge 判 PASS | 多数表决 |
| `weighted_avg` | 加权均分 ≥ threshold | 多 judge 给的是连续分而非二元判断时 |
| `avg` | 简单均分 ≥ threshold | 多 judge 连续分简单平均 |
| `any_pass` | 至少一个 judge 判 PASS | 鼓励探索 / 高召回场景（最宽松） |

### 5.3 `frontier_type` 4 种取值

| 取值 | 含义 | 适用 |
| --- | --- | --- |
| `instance` | 每个 case 维护一个 best 候选 | 单 metric 或简单业务 |
| `objective` | 每个 metric 维护一个 best 候选 | 多 metric 但 case 量少 |
| `hybrid` | 同时维护 case + metric 双层前沿 | **多 metric 真冲突场景**（本 example 推荐） |
| `cartesian` | 每个 (case, metric) 组合一个 best | 极复杂 / 调试用，常导致候选池爆炸 |

`hybrid` 让 GEPA 在改进一个 metric 时不丢失另一个 metric 上的最佳候选，是多 metric 业务的默认推荐。

### 5.4 `stop.required_metrics` 3 种取值

| 取值 | 语义 |
| --- | --- |
| `"all"`（默认） | val 集上**所有** metric 都达 threshold 才早停 |
| `["m1", "m2"]` | 列出的 metric 全部达 threshold 才早停（其他 metric 仍参与评测但不影响早停） |
| `null` 或 `[]` | 不参与早停，仅靠算法层 budget / no-improvement / score_threshold 控制 |

本 example 显式用列表形式列出两条 metric。当业务 metric 较多但只有部分作为早停门禁时，列表形式比 `"all"` 更精准。

### 5.5 `eval_case_parallelism` 与 multi-judge 的相互作用

multi-judge × `num_runs=2` 使每条 case 一次评测约 8 次 LLM 调用。若 `eval_case_parallelism=4`（默认）+ 训练集 5 case，单轮可能产生 ~40 个 judge 请求并发，容易撞 LLM 后端的 rate limit。本 example 设为 `1` 串行执行；业务可根据 LLM 后端 QPS 上调。

## 6 · 调试技巧

### 6.1 查看反思 LM 看到的多 metric 反馈

`run_optimization.py` 中 `verbose=1` 改为 `verbose=2`，gepa 内部日志会附带反思 prompt，可看到反思 LM 实际接收的 metric 反馈结构。

### 6.2 优雅停止

```bash
touch runs/<timestamp>/optimize.stop
```

## 7 · 常见问题

**Q：`all_pass` 是不是太严格了？**
A：取决于业务诉求。合规 / 安全场景下 "any judge raises a red flag" 应该立即拦截，`all_pass` 是合理选择。质量评估场景可换 `majority_pass` 或 `weighted_avg`。

**Q：3 个 judge 用同一个模型只是 temperature 不同，有意义吗？**
A：有部分意义——不同 temperature 触发不同采样路径，可发现一些边界情况。但更稳健的做法是混用不同模型族（如 GPT + Claude + GLM），可显著降低同源偏差。

**Q：reflection LM 与 judge 用同一个模型会"自评"吗？**
A：`llm_rubric_response` 的 judge 看的是预定义 rubric 文本，受偏差影响较小。`llm_final_response` 的 judge 看 reference 答案做实质等价判断，相对客观。生产环境建议至少 judge 与 agent 模型不同源，参见 quickstart §5.1。

**Q：`num_runs` 调高会不会降低优化效率？**
A：会。`num_runs=2` 让每条 case 评测耗时翻倍，但能消除一定 LLM 输出方差（同一 prompt 同一 case 两次跑分不一致），通常对收敛稳定性有正向作用。`num_runs=1` 适合追求速度的早期实验，`num_runs=2–3` 适合接近收尾的精打磨。

**Q：rubric 数量太多怎么办？**
A：单 judge 一次打多 rubric 时若 rubric > 6–8 条，judge 输出质量下降明显。建议拆成多条 `llm_rubric_response` metric，每条聚焦 2–4 条相关 rubric。

## 8 · 接入自有业务的步骤

1. **梳理业务约束**：哪些是硬约束（必须通过）、哪些是软约束（按比例打分）
2. **选择 metric 类型**：硬约束用 `llm_final_response` + `all_pass`；软约束用 `llm_rubric_response` 多 rubric
3. **配置 multi-judge**：`judge_models` 数组形式；选择合适的 `models_aggregator`
4. **设置 `stop.required_metrics`**：列出哪些 metric 决定何时早停
5. **启用 `frontier_type="hybrid"`**：多 metric 场景的默认推荐
6. **调整数据集**：`evalset` 中的 `final_response` / `reference` 字段需配合 metric 类型
7. **控制并发**：`eval_case_parallelism` 设为 LLM 后端能承受的 QPS / 单 case judge 调用数
8. **运行并观察**：`result.json` 中 `metric_breakdown` 显示每条 metric 独立分数，便于诊断瓶颈
