# Quickstart — `AgentOptimizer` 入门示例

> **适用场景**：首次使用 `AgentOptimizer`，需要在最小完整流程下理解 prompt 自动优化的输入、输出与基本工作机制。本文档是后续 9 个 example 的前置阅读材料，所有进阶 example（HTTP 服务接入、远端 prompt 源、多 agent 链路等）默认假设读者已熟悉本文涉及的概念。

## 1 · 适用问题与设计目标

迭代 prompt 是 LLM agent 工程中重复成本最高的环节之一：手动改 prompt → 重跑评估 → 根据失败用例再改，循环数十次。`AgentOptimizer` 将该循环自动化：

| 输入 | 输出 |
| --- | --- |
| 一个支持热加载 prompt 的 agent | 满足 metric 阈值的最优 prompt 候选集 |
| 训练集（反思样本来源） + 验证集（候选评分依据） | `result.json`（机器可读）+ `summary.txt`（人类可读）+ 每轮过程产物 |
| 一组 metric（精确匹配 / 正则 / LLM 裁判 / 多 metric 组合） | baseline → best 的端到端分数对比 |

底层算法采用 **GEPA**（reflective prompt mutation），由一个独立的 reflection LLM 检视 agent 在训练集上的失败用例，生成候选 prompt；候选先在验证集上全量评估，再与历史 Pareto 前沿比较，决定是否接受。

### 本 example 演示的最小用例

| 维度 | 值 |
| --- | --- |
| 业务任务 | 小学算术应用题求解 |
| 优化目标 | `agent/prompts/system.md`（角色定义） + `agent/prompts/skill.md`（解题方法论） |
| 验证指标 | `final_response_avg_score`（精确匹配，阈值 1.0） + `llm_rubric_response`（三条评分标准均值，阈值 0.66） |
| 训练 / 验证规模 | 5 条 / 3 条 |

`system.md` 与 `skill.md` 的 baseline 内容刻意制造冲突（前者要求"只输出答案"，后者要求"展开推理"），以确保 GEPA 必须改写至少其中一个文件才能让两条 metric 同时达标——这一设计让反思机制的作用对读者直接可见。

## 2 · 术语对照

下列术语在后续章节首次出现时不再展开解释，请先建立认知。

| 术语 | 含义 |
| --- | --- |
| **GEPA** | Genetic-Pareto reflective prompt optimization。本 SDK 默认且当前唯一收录的优化算法。 |
| **TargetPrompt** | 声明哪些 prompt 字段会被优化器读写的注册表。每个字段对应一个本地文件（`add_path`）或一对异步 `read/write` 回调（`add_callback`）。 |
| **call_agent** | 用户提供的回调，签名固定为 `async def(query: str) -> str`。框架通过它驱动 agent 完成单次推理。 |
| **eval set** | 评估用例集合。`train` 用于反思 minibatch 抽样，`val` 用于决定候选是否接受、是否触发早停。 |
| **metric** | 通过 / 失败标准，可组合使用。本 SDK 内置 `final_response_avg_score`、`llm_final_response`、`llm_rubric_response`、`trajectory_avg_score` 等。 |
| **reflection LM** | 负责检视失败用例并生成新候选 prompt 的 LLM。可与 agent 共用模型，亦可独立配置。 |
| **judge model** | LLM 裁判，按 rubric 对 agent 输出打分。 |
| **minibatch** | 每轮反思从 train 集随机抽取的若干 case，用于驱动 reflection LM。 |
| **Pareto 前沿** | 在任一 metric 上是历史最优的候选都被保留，作为下一轮反思的潜在亲本。 |
| **stop condition** | 终止优化的判定条件。SDK 同时支持算法层（budget / no-improvement / score threshold 等）与框架层（`stop.required_metrics`）两类。 |

## 3 · 运行示例

### 3.1 安装可选依赖

```bash
pip install -e ".[optimize]"
```

`optimize` extra 包含 `gepa`（反思算法实现）与 `rich`（终端进度面板）。`rich` 缺失时进度面板自动降级为纯文本。

### 3.2 配置环境变量

```bash
export TRPC_AGENT_API_KEY="<your-key>"
export TRPC_AGENT_BASE_URL="<your-endpoint>"
export TRPC_AGENT_MODEL_NAME="<your-model>"
```

默认情况下 agent、reflection LM、judge model 共用同一组凭据。如需为 judge 配置独立模型，参见 §6.3。

### 3.3 启动

```bash
python examples/optimization/quickstart/run_optimization.py
```

终端将依序输出：baseline 评估分数 → 每轮 GEPA 反思的接受 / 拒绝记录 → 收尾摘要（含 `stop_reason`）。

### 3.4 产物结构

```
runs/<timestamp>/
├── result.json           完整运行记录，机器可读
├── summary.txt           人类可读的总览
├── baseline_prompts/     运行前的 prompt 快照（用于回滚与对照）
├── best_prompts/         val 集上得分最高的候选
└── rounds/               每轮的反思 prompt、候选文本、评估明细
```

推荐阅读顺序：先看 `summary.txt` 了解总体走向，再用 `diff -r baseline_prompts/ best_prompts/` 查看 prompt 实际变更内容。

## 4 · 架构与数据流

```
                ┌────────────────────────────────┐
                │  AgentOptimizer.optimize(...)  │
                └───────────────┬────────────────┘
                                │
        ┌───────────────────────┼─────────────────────────────┐
        ▼                       ▼                             ▼
  baseline 评估           GEPA 主循环                       收尾产物
  ─────────────           ───────────                       ────────
  当前 prompt             每轮：                            best_prompts/
  在 val 集上的           ① module_selector 选定字段         result.json
  起始分数                ② 抽 train minibatch              summary.txt
                          ③ reflection LM 生成新候选         rounds/*.json
                          ④ 候选在 val 集上全量评估
                          ⑤ 与 Pareto 前沿比较
                          ⑥ 接受 / 拒绝候选
                          ⑦ 触发停止条件检查
```

### 4.1 文件清单

| 文件 | 角色 | 接入自有业务时的修改方向 |
| --- | --- | --- |
| `run_optimization.py` | 入口脚本，注册 `TargetPrompt`、定义 `call_agent` | 替换 `call_agent` 实现以驱动自有 agent |
| `agent/agent.py` | `LlmAgent` 工厂，每次调用重读 prompt | 替换为自有 agent 构建逻辑（模型、tools、output schema 等） |
| `agent/prompts/system.md` | 角色定义 prompt（GEPA 写入目标） | 写入业务 baseline；可作为初始版本起点 |
| `agent/prompts/skill.md` | 方法论 prompt（GEPA 写入目标） | 单字段优化时可整体删除 |
| `optimizer.json` | 算法 + metric 配置 | 调整 metric 类型、阈值、停止条件 |
| `train.evalset.json` | 反思 minibatch 来源 | 替换为业务训练用例 |
| `val.evalset.json` | 候选评分依据 | 替换为业务验证用例 |

### 4.2 prompt 拆分的设计动机

将 prompt 拆为 `system.md` 与 `skill.md` 两个独立文件，对应 `TargetPrompt` 的两个 key（`"system_prompt"` 与 `"skill"`）。`module_selector="round_robin"` 配置下，GEPA 每轮仅改写其中一个文件，便于：

- **归因**：可直接定位是哪个文件的改动带来分数提升
- **稳定性**：单字段改动比多字段同改更易被验证集接受
- **演示价值**：刻意冲突的 baseline 强制 GEPA 至少改写一个文件，否则 metric 无法同时达标

若业务只需优化单文件 prompt，移除第二个 `add_path` 调用即可。

## 5 · 关键配置

`optimizer.json` 中以下字段直接影响优化效率与产物质量：

| 字段 | 默认 / 本 example 值 | 影响 |
| --- | --- | --- |
| `evaluate.num_runs` | 1 | 每条 case 的推理次数。提高至 2–3 可平滑 LLM 输出方差，代价为评估耗时线性增长 |
| `optimize.eval_case_parallelism` | 2 | 单批 case 的最大并发推理数。LLM 后端有 QPS 限制时需调小 |
| `optimize.stop.required_metrics` | `"all"` | 框架层早停：`"all"` = 所有 metric 达标；列表 = 仅指定 metric 达标即可；`null`/`[]` = 完全交由算法层停止条件决定 |
| `optimize.algorithm.reflection_minibatch_size` | 3 | 每轮反思的 case 数。过小会导致反思素材单调；过大单轮耗时增加 |
| `optimize.algorithm.skip_perfect_score` | `false` | 是否跳过已满分的 case。小训练集建议保持 `false`，否则 minibatch 容易反复抽到同一条 case |
| `optimize.algorithm.max_metric_calls` | 60 | 累计 case 评估次数上限，控制总开销的主要手段 |
| `optimize.algorithm.max_iterations_without_improvement` | 8 | 连续 N 轮 val 分无提升即提前停止 |

> JSON 标准不支持 `//` 注释，配置文件中使用本表说明替代行内注释。

### 5.1 分离 judge 模型

`optimizer.json` 中 `evaluate.metrics[*].criterion.llm_judge.judge_model` 可独立配置，与 agent 凭据互不影响：

```json
"judge_model": {
  "provider_name": "openai",
  "model_name": "<judge-model-name>",
  "api_key": "<judge-api-key>",
  "base_url": "<judge-endpoint>"
}
```

### 5.2 启用 reflection / judge 的思考模式

`OptimizeModelOptions` 与 `JudgeModelOptions` 均支持三态 `think` 字段：

| 取值 | 行为 |
| --- | --- |
| `null`（默认） | 沿用模型默认配置，不做修改 |
| `true` | 注入 `BuiltInPlanner(ThinkingConfig(include_thoughts=True, thinking_budget=-1))`，并在 `http_options.extra_body` 写入 `chat_template_kwargs.enable_thinking=true`（兼容 GLM 等 OpenAI 兼容后端） |
| `false` | 显式关闭思考模式 |

## 6 · 运行控制

### 6.1 优雅停止

`Ctrl+C` 可能截断当前轮的产物文件。需要中途收尾时建议改用 stop 文件：

```bash
touch runs/<timestamp>/optimize.stop
```

下一次 stopper 检查时框架立即收尾，所有已完成轮次的 artifact 完整落盘，`OptimizeResult.stop_reason` 标记为 `user_requested_stop`。

### 6.2 update_source 的语义

`AgentOptimizer.optimize(update_source=False)`（默认）下源 prompt 文件保持不变，最优候选仅写入 `runs/<timestamp>/best_prompts/`。若需在优化成功后直接覆盖源文件（典型于 CI/CD 闭环场景，参见 `ci_integration/` example），将该参数置为 `True`。

## 7 · 常见问题

**Q：多轮对话 case 在优化时是否保留上下文？**
A：默认不保留。`call_agent` 每次调用使用独立的 `Runner + InMemorySessionService`。需要真实多轮上下文时，需在 `call_agent` 内部自行维护 session 状态——典型做法是用 `contextvars.ContextVar` 存放当前 case 的 history（`asyncio.Task` 启动时自动 `copy_context`，并发评估下天然按 task 隔离）。

**Q：reflection LM 与 agent 共用模型是否会引入"自评"偏差？**
A：`llm_rubric_response` 让 judge 依据预定义 rubric 文本打分，而非自由评价，可缓解大部分偏差。生产环境建议 judge 配置独立模型，参见 §5.1。

**Q：`best_prompts/` 中的文件就是最终产物吗？**
A：是 val 集上得分最高的候选。`update_source=False` 时源文件不变，需手动复制；`update_source=True` 时源文件被自动覆盖（仅在 `OptimizeResult.status=SUCCEEDED` 时触发）。

**Q：运行结束后 baseline 与 best 分数无变化（无收敛）该如何排查？**
A：按以下顺序检查：
1. baseline prompt 是否过于简单，导致 reflection LM 无明确改进方向
2. `reflection_minibatch_size` 是否过小，导致反思素材单调
3. metric 阈值是否设置过高（如 1.0 要求全 case 完美匹配）
4. 直接阅读 `runs/<timestamp>/rounds/round_*.json` 中的 reflection LM 原始输出，常可定位具体原因

**Q：单次运行的开销估算？**
A：本 example 默认配置下约 5 分钟、约 60 次 LLM 调用。`max_metric_calls=60` 是硬性上限，超出立即停止。

## 8 · 接入自有 agent 的步骤

1. 替换 `agent/prompts/*.md` 为业务 baseline prompt
2. 修改 `agent/agent.py` 中 `create_agent()` 实现，对接业务模型 / tools / output schema
3. 替换 `train.evalset.json` 与 `val.evalset.json` 为业务用例
4. 调整 `optimizer.json` 中 metric 类型与阈值
5. 运行 `run_optimization.py`，根据 `summary.txt` 与 `result.json` 决定是否继续调参

若业务 agent 的形态不同于本 example（HTTP 服务、远端 prompt 源、多 agent 编排、CLI 黑盒等），请参考 `examples/optimization/` 下对应专题示例。
