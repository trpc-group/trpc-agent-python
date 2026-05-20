# Multi-Agent Pipeline — 多 sub-agent prompt 联合优化

> **适用场景**：业务侧已编排好多 sub-agent 协作链路（router / 分支 worker / summarizer 等），希望在不修改链路代码的前提下，对每个 sub-agent 的 prompt 进行联合优化。本 example 在 `quickstart/` 单字段优化的基础上，演示多字段 `TargetPrompt` 与 GEPA 多模块协同的关键配置。阅读前请先熟悉 `quickstart/README.md` §2 中的基础术语。

## 1 · 适用问题与设计目标

多 agent 链路的 prompt 工程比单 agent 复杂：每个 sub-agent 有独立 prompt，字段间存在隐式契约（router 的输出标签必须匹配下游 worker 期望、summarizer 的格式必须兼容上游中间结果）。手工迭代时常见症状是"改 A 见效，但拖累 B"。

本 example 的设计原则：

- **链路代码零修改**：优化器通过文件写入候选 prompt，sub-agent 在每次调用时现读现用
- **字段间归因清晰**：`module_selector="round_robin"` 让每轮反思只改一个字段
- **多字段成果融合**：`use_merge=true` 在累积若干单字段改动后主动尝试合并
- **跨字段记忆延展**：`reflection_history_top_k=3` 让反思 LM 在轮换中保留更长历史

| 输入 | 输出 |
| --- | --- |
| 已编排好的多 sub-agent 链路（本 example 中为 `invoke_pipeline()`） | 每个 sub-agent 的最优 prompt 候选（`best_prompts/` 下多个 `.md` 文件） |
| 同一个 `TargetPrompt` 上注册的多个字段（每字段一个 `add_path`） | 单一 `final_response_avg_score` metric 的端到端分数提升 |

### 本 example 演示的最小用例

| 维度 | 值 |
| --- | --- |
| 业务任务 | 混合事实题与算术题分流问答 |
| 链路结构 | `router → fact_agent / math_agent → summarizer`（共 4 个 sub-agent） |
| 优化目标 | `pipeline/prompts/{router,fact_agent,math_agent,summarizer}.md` 共 4 个字段 |
| 验证指标 | `final_response_avg_score`（contains 匹配，要求最终答复包含 `答案：xxx`） |
| 训练 / 验证规模 | 5 条混合 case（3 事实 + 2 数学）/ 3 条混合 case |

## 2 · 术语对照

仅列出本 example 引入的新概念。基础术语见 `quickstart/README.md` §2。

| 术语 | 含义 |
| --- | --- |
| **多字段 TargetPrompt** | 同一 `TargetPrompt` 实例上多次调用 `add_path()`，每次注册一个独立 prompt 文件。GEPA 视每个 key 为一个独立的可优化模块（component / predictor）。 |
| **module_selector** | 每轮反思选择哪个字段被改写的策略。`"round_robin"` 按注册顺序逐轮单选；`"all"` 每轮全选；`"random"` 随机单选。多字段优化推荐 `round_robin`。 |
| **use_merge** | 是否启用 GEPA 的 merge 操作。每隔若干轮，算法挑选两个在不同字段上各有所长的历史候选，融合成一个"全字段都好"的新候选。**仅多 predictor（多字段）时有意义**——单字段优化不会触发 merge。 |
| **max_merge_invocations** | 整个 run 中允许触发 merge 的次数上限。 |
| **reflection_history_top_k** | 反思 prompt 中每条 case 携带多少条历史最佳响应。多字段轮换时调大可缓解"上次改某字段时学到的方向被遗忘"。 |
| **Other Active Components** | SDK 自动注入到反思 prompt 的段落。当反思 LM 在改字段 X 时，该段落列出所有其他字段（Y / Z / ...）当前的内容，使 LM 在改 X 时能感知链路其他环节的现状。无需配置。 |

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
python examples/optimization/multi_agent_pipeline/run_optimization.py
```

单次运行约 10–20 分钟。每条 case 触发 3 次 LLM 推理（router → 分支 worker → summarizer），整体 LLM 调用量约为 quickstart 的 3 倍。

### 3.4 产物结构

```
runs/<timestamp>/
├── result.json           完整运行记录（含每轮 optimized_field_names / kind）
├── summary.txt           人类可读摘要
├── baseline_prompts/     运行前 4 个 prompt 文件的快照
├── best_prompts/         val 集得分最高的候选（4 个 .md）
└── rounds/round_*.json   每轮反思 prompt、候选文本、字段轮换记录
```

## 4 · 架构与数据流

```
[run_optimization.py]
    │
    ├── TargetPrompt
    │     .add_path("router",     pipeline/prompts/router.md)
    │     .add_path("fact_agent", pipeline/prompts/fact_agent.md)
    │     .add_path("math_agent", pipeline/prompts/math_agent.md)
    │     .add_path("summarizer", pipeline/prompts/summarizer.md)
    │
    ├── call_agent(query) = await invoke_pipeline(query)
    │     ├─ router       (读 router.md)        → 输出 "fact" 或 "math"
    │     ├─ fact / math  (读对应 .md)          → 中间答复
    │     └─ summarizer   (读 summarizer.md)    → 最终答复（含 "答案：xxx"）
    │
    └── AgentOptimizer.optimize 主循环
        ├── module_selector="round_robin" 每轮选一个字段改写
        ├── 把候选 prompt 写入对应文件 → 下一次 invoke_pipeline 自动读到
        ├── use_merge=true：每隔若干轮主动融合不同字段的历史最佳
        └── 反思 prompt 自动包含 Other Active Components 段
```

### 4.1 文件清单

| 文件 | 角色 | 接入自有业务时的修改方向 |
| --- | --- | --- |
| `run_optimization.py` | 优化器入口，注册 4 字段 `TargetPrompt`，定义 `call_agent` | 将 `invoke_pipeline` 替换为业务自有链路调用入口 |
| `pipeline/orchestrator.py` | 链路编排实现，每个 sub-agent 在每次调用时重读 prompt 文件 | 真实业务下整体替换为业务链路代码 |
| `pipeline/prompts/router.md` 等 4 文件 | 各 sub-agent 的 system prompt（GEPA 写入目标） | 替换为业务 baseline；每字段对应 `TargetPrompt` 中一个 key |
| `optimizer.json` | 算法 + metric 配置 | 重点关注 `module_selector` / `use_merge` / `reflection_history_top_k` |
| `train.evalset.json` / `val.evalset.json` | 数据集 | 替换为业务用例 |

### 4.2 prompt 热加载约束

每个 sub-agent 在每次被调用时必须重读自己的 prompt 文件，否则优化器写入的新候选不会生效。`pipeline/orchestrator.py` 的 `_create_sub_agent()` 在每次 `invoke_pipeline()` 中重新构造 sub-agent 实例并重读对应 `.md` 文件实现该语义。

## 5 · 关键配置

`optimizer.json` 中本 example 与 quickstart 的核心差异：

```jsonc
{
  "optimize": {
    "algorithm": {
      "module_selector": "round_robin",       // 多字段轮换的关键
      "use_merge": true,                      // 多字段成果融合
      "max_merge_invocations": 3,             // merge 次数上限
      "reflection_history_top_k": 3,          // 多字段轮换时调大
      "reflection_minibatch_size": 2,
      "max_metric_calls": 60
    }
  }
}
```

### 5.1 `module_selector` 选择对照

| 取值 | 行为 | 适用 |
| --- | --- | --- |
| `"round_robin"` | 每轮按注册顺序单选 1 个字段 | 字段间存在依赖；需要清晰归因（推荐） |
| `"all"` | 每轮所有字段一起改 | 字段独立、希望快速搜索；存在"一个改坏拖累整体"风险 |
| `"random"` | 每轮随机单选 1 个字段 | 字段无明显依赖、希望均匀探索 |

### 5.2 `use_merge` 在多字段场景的价值

`round_robin` 让每轮只改 1 个字段，几轮后会出现"router 改好了但 summarizer 还差 / fact_agent 改好了但 math_agent 还差"的局面。`use_merge=true` 让 GEPA 隔几轮主动尝试合并——例如把"router 优化版"和"summarizer 优化版"融合成"全字段都好"的候选。

> **重要约束**：merge 是 predictor-level 操作，**仅多 predictor（多字段）场景生效**。单字段优化下 `use_merge=true` 永远不会触发 merge round，配置无副作用但也无收益。

`max_merge_invocations` 限制合并尝试次数，避免无限拼接。

### 5.3 `reflection_history_top_k` 在多字段场景的价值

多字段轮换时反思 LM 在第 N 轮改 `summarizer`，但 `router` 是几轮前才改过的——LM 容易遗忘"上次改 router 时学到的方向"。`reflection_history_top_k=3`（默认 2）让反思 prompt 中每条 case 携带历史最佳响应 3 条，相当于给 LM 提供"过去几轮哪些方向奏效"的记忆。

## 6 · 调试技巧

### 6.1 验证 round-robin 真的轮流改字段

跑完后检查 `runs/<timestamp>/result.json` 中各 round 的 `optimized_field_names`，应按 `router → fact_agent → math_agent → summarizer → router → ...` 顺序循环。

### 6.2 验证 merge 是否触发

各 round 的 `kind` 字段：`"reflective"` 是普通反思轮，`"merge"` 是融合轮。

### 6.3 查看反思 prompt 的 Other Active Components

在 `result.json` 的 round detail 中，反思 prompt 文本可见 Other Active Components 段落，列出当前轮次以外的所有字段当前内容。

## 7 · 常见问题

**Q：链路必须由本框架的 LlmAgent 编排吗？**
A：不必。`call_agent` 只要求 `async (query: str) -> str` 签名。可以让它把 query 透传给 HTTP 请求 / gRPC 调用 / 内部 SDK / 其他编排框架。本 example 用 `invoke_pipeline` 仅作演示，业务可以替换为任何形态。

**Q：每个 sub-agent 必须在同一进程吗？**
A：不必。每个 sub-agent 可以是独立服务，prompt 通过配置中心而非本地文件下发——把 `add_path` 替换为 `add_callback`，参见 `remote_prompt_store/` example。

**Q：单 case 经过多次 LLM 推理，评测开销很大如何控制？**
A：调小 `eval_case_parallelism` 防止 LLM rate limit；调小 `reflection_minibatch_size` 减少每轮 case 数；调小 `max_metric_calls` 限制总预算。

**Q：`use_merge=true` 但 `merge_rounds=0`？**
A：单字段优化下 merge 不会触发；多字段场景下也需累积若干轮反思后才会有候选满足 merge 条件。耐心运行至少 `max_merge_invocations` 轮以上观察。

## 8 · 接入自有链路的步骤

1. **替换 `pipeline/orchestrator.py`**：实现自己的 `invoke_pipeline(query) -> str`，可以是 HTTP / gRPC / 内部编排
2. **修改 prompt 文件路径**：把每个 sub-agent 的 prompt 文件路径作为 `TargetPrompt.add_path` 的参数注册
3. **保证 prompt 热加载**：每个 sub-agent 在每次被调用时重读 prompt（或重新拉配置中心）
4. **替换数据集与 metric**：`train.evalset.json` / `val.evalset.json` / `optimizer.json`
5. **运行**：根据 `result.json` 中的 `optimized_field_names` / `kind` 序列分析字段轮换与 merge 行为

若 prompt 不在本地而在配置中心，把 `add_path` 替换为 `add_callback`，其余结构保持不变。
