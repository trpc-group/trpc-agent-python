# Eval-Optimize Loop — 评测 · 归因 · 优化 · 回归 · 审计 自动闭环

[English version](README.en.md) ｜ 方案设计说明见 [DESIGN.md](DESIGN.md)

> **零 API Key 可跑**：`python run_pipeline.py --scenario all`，三个场景全流程 < 1 分钟。

## 1 · 适用问题与设计目标

`AgentOptimizer` 能自动改出分数更高的 prompt，但「分数变高」不等于「值得上线」：

- 优化器看到的可能是**弱指标**（黑盒模式只有响应匹配，没有工具轨迹/知识召回）；
- 优化器的调参集如果与训练集同源，它会**过拟合**而毫无察觉；
- 没有逐 case 对比，你不知道提升是不是靠「牺牲原本通过的关键 case」换来的；
- 没有审计产物，改出来的 prompt 即使分数变高也难以进入生产评审。

本 example 把 `AgentEvaluator` 与 `AgentOptimizer` 拼成一条**可复现的六阶段闭环**，
回答唯一的问题：**这个候选 prompt 到底值不值得接受？**

```
① baseline 评测           ② 失败归因            ③ 优化执行
train+val × 4 metric  →  6 类失败类型聚类   →  AgentOptimizer(GEPA)
（逐 case 分数/轨迹）      （每案给可读理由）      （优化 2 个 TargetPrompt 字段）
                                                      │
⑥ 审计落盘            ⑤ 接受策略             ④ 候选验证
报告 json+md      ←  六道可配置闸门      ←  独立 train/val 复评
每轮候选/成本/seed     （全过才接受）           逐 case delta 对比
```

### 本 example 演示的最小用例

「城市信息助手」：距离换算（`convert_distance` 工具）、城市介绍（`knowledge_search`
工具 + 来源标注）、身份询问（无工具）。baseline prompt 有三处缺陷（单位不归一化、
不检索知识库、不按 JSON 输出），6 条评测 case（3 train + 3 val）刚好暴露全部缺陷。
三个内置场景对应三种典型结局：

| 场景 | 优化器提出的候选 | 独立验证集复评 | gate 决策 |
| --- | --- | --- | --- |
| `success` | 修复全部三处缺陷 | train 1/3→3/3，val 1/3→3/3 | ✅ ACCEPT |
| `no_effect` | 只有文案润色（指令不变） | 全部 unchanged | ❌ REJECT（提升不足） |
| `overfit` | 死记硬背训练样本 | train 1/3→3/3，**val 1/3→0/3** | ❌ REJECT（过拟合守卫） |

## 2 · 术语对照

| 术语 | 含义 |
| --- | --- |
| 验收套件 | `data/eval_config.json`：轨迹 + 精确响应 + rubric + 知识召回 4 个 metric，回归评测用 |
| 优化器弱指标 | `optimizer.json` 里只有 `final_response_avg_score`：黑盒 `call_agent` 模式下 SDK 禁用轨迹/召回 metric，这个信息差正是过拟合演示的一环 |
| 泄漏调参集 | `data/optimizer_probe.evalset.json`：与训练集同分布，仅 overfit 场景喂给优化器当"验证集"，演示其危害 |
| 保护 case | `pipeline.json` 的 `protected_cases`：关键 case 白名单，任何退化直接拒绝 |
| 指令 DSL | prompt 里的 `<!-- directives: ... -->` 注释块，fake agent 据此改变行为，让"改 prompt"离线也有真实行为差异 |
| trace 模式 | evalset 里的 `evalMode: "trace"`：用预录轨迹评测归因，不执行 agent |

## 3 · 运行示例

### 3.1 零依赖运行（无需任何环境变量 / API Key）

```bash
# 默认 success 场景
python examples/optimization/eval_optimize_loop/run_pipeline.py

# 三场景全跑（推荐第一次看）
python examples/optimization/eval_optimize_loop/run_pipeline.py --scenario all

# baseline 额外用预录轨迹（trace 模式）评测归因，不执行 agent
python examples/optimization/eval_optimize_loop/run_pipeline.py --baseline-from-trace

# gate 通过时把最优候选写回 loop_agent/prompts/（会改动源文件，谨慎；
# --scenario all 时等全部场景跑完后统一写回，不污染后续场景 baseline）
python examples/optimization/eval_optimize_loop/run_pipeline.py --apply

# 校验报告字段契约
python examples/optimization/eval_optimize_loop/run_pipeline.py --check sample_output/success/optimization_report.json
```

运行测试：

```bash
python -m pytest examples/optimization/eval_optimize_loop/tests -q
```

### 3.2 产物结构

```
runs/<场景>-<时间戳>/
├── optimization_report.json       # 结构化报告：baseline / candidate / delta / 归因 / gate 决策
├── optimization_report.md         # 人话版：是否值得接受 + 全部依据
├── baseline_eval.json             # 阶段① 逐 case 原始记录
├── candidate_eval.json            # 阶段④ 逐 case 原始记录
├── attribution.json               # 阶段② 归因明细
├── pipeline_config.snapshot.json  # 本次运行的 gate/seed 配置快照
└── optimize/                      # 阶段③ SDK 原生审计目录
    ├── result.json  summary.txt  run.log  config.snapshot.json
    ├── rounds/round_001.json …    # 每轮候选 prompt、接受理由、成本、耗时
    └── baseline_prompts/  best_prompts/
```

提交在仓库里的 `sample_output/` 就是 `--scenario all` 的三份报告；重新生成：

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py --scenario all --output /tmp/regen
# 然后把 /tmp/regen/<场景>-*/optimization_report.{json,md} 拷入 sample_output/<场景>/
```

## 4 · 输入 / 输出文件清单

| 文件 | 角色 |
| --- | --- |
| `data/train.evalset.json` | 训练集 3 条（优化器反思 minibatch 来源） |
| `data/val.evalset.json` | 独立验证集 3 条（回归复评的最终裁判） |
| `data/optimizer_probe.evalset.json` | 泄漏调参集 3 条（仅 overfit 场景喂给优化器） |
| `data/trace_baseline.evalset.json` | trace 模式演示 2 条（预录 baseline 失败轨迹） |
| `data/eval_config.json` | 验收 metric 套件（4 metric，fake judge） |
| `optimizer.json` / `configs/optimizer.*.json` | 优化配置（三场景仅 `reflection_lm.model_name` 不同） |
| `pipeline.json` | 闸门阈值 / 保护 case / 预算 / seed |
| `loop_agent/prompts/system.md`、`skill.md` | 两个 TargetPrompt 源文件（system prompt + skill prompt） |
| `candidates/*.md` | fake 反思 LM 的候选提案库（场景 × 字段） |
| `sample_output/*/optimization_report.{json,md}` | 三场景示例报告 |

## 5 · gate 决策规则表（阶段⑤）

六道闸门全过才接受；拒绝理由按严重度取最关键闸门（过拟合 > 保护 case > 新增
hard fail > 提升不足 > 预算）。全部阈值都在 `pipeline.json` 里可配。

| 闸门 | 规则 | 对应配置 |
| --- | --- | --- |
| `min_val_improvement` | 验证集通过率提升 ≥ 阈值 且 平均分提升 ≥ 阈值 | `min_val_pass_rate_improvement` / `min_val_score_improvement` |
| `no_new_hard_fail` | 不允许任何 case pass→fail | `forbid_new_hard_fail` |
| `protected_cases` | 保护 case 出现 new_fail / score_down 即拒绝 | `protected_cases` |
| `overfit_guard` | train 通过率↑ 且 val 通过率↓ → 判定过拟合 | `overfit_guard` |
| `cost_budget` | 优化成本 ≤ 预算；metric 调用数 ≤ 预算（可选） | `max_cost_usd` / `max_metric_calls` |
| `duration_budget` | pipeline 墙钟时长 ≤ 预算 | `max_duration_seconds` |

决策矩阵的 12 组期望行为见 `tests/test_gates.py::DECISION_MATRIX`（12/12 通过）。

## 6 · 失败归因规则表（阶段②）

规则只依赖框架 metric 结果的结构，不依赖本 example 的具体 case（隐藏样本同样适用）；
每个失败 case **保证至少一条**带证据的中文理由（规则不覆盖时按 metric 兜底映射）。

| 失败类型 | 触发规则 |
| --- | --- |
| `wrong_tool_call` 工具调用错误 | 轨迹 metric 失败且实际/期望调用**名字多重集**不同（漏调/多调/调错） |
| `wrong_tool_args` 工具参数错误 | 轨迹 metric 失败且名字一致、参数不同 |
| `knowledge_recall_miss` 知识召回不足 | 召回 rubric 失败；或漏调的正是知识检索工具（与轨迹归因并报） |
| `format_violation` 格式不符合要求 | 响应 metric 失败且期望可解析为 JSON、实际不能 |
| `llm_rubric_fail` LLM rubric 不达标 | 回答质量 rubric 失败（证据 = 未通过的 rubric id + 理由） |
| `final_answer_mismatch` 最终回复不匹配 | 响应 metric 失败的其余情况 |

主要归因（根因）优先级：`wrong_tool_call` > `wrong_tool_args` > `knowledge_recall_miss`
> `format_violation` > `llm_rubric_fail` > `final_answer_mismatch`（轨迹错误在链路上游）。

## 7 · 设计要点

### 7.1 为什么优化器指标 ≠ 验收指标（刻意的信息差）

`AgentOptimizer` 的黑盒 `call_agent` 模式拿不到工具轨迹与工具返回，SDK 会**硬性拒绝**
在该模式下配置 `tool_trajectory_avg_score` / `llm_rubric_knowledge_recall`。所以
`optimizer.json` 只有响应精确匹配 —— 这正是真实业务的常态：优化器看到的信号弱于
验收套件。闭环的意义就在于此：**优化器说变好了，还要用完整验收套件在独立验证集上
复评过才算数**。overfit 场景把这个信息差推到极端（优化器视角 0/3→3/3，独立复评
val 1/3→0/3）。

### 7.2 三个 fake 模型如何做到零 API Key（不改一行 SDK）

框架的 judge / reflection LM 配置都支持 `provider_name`；非 openai 的 provider 会走
`ModelRegistry.create_model("{provider}/{model}")` 正则路由。本 example 注册三个
fake provider（`fake-agent` / `fake-judge` / `fake-reflection`），全部是确定性规则实现：

- **fake agent** 解析 prompt 里的指令 DSL（`<!-- directives: ... -->`）决定行为 ——
  于是「优化 prompt」在离线环境下也有真实的行为/分数差异；
- **fake judge** 按 rubric 文本里的反引号 token 判定，条件规则（「如果…」+ 条件不适用
  => yes）与真实裁判 prompt 对齐，输出 JSON 与真实裁判同构、走 SDK 原生解析器；
- **fake reflection** 依据 prompt 顶部的 `<!-- prompt-field: X -->` 标记返回
  `candidates/X.<场景>.md`，场景取自自己的 `model_name`。

### 7.3 为什么包名是 `loop_agent` / `loop_pipeline`

pytest 可能在同一进程 import 多个 example 的包：`agent` 被多数 example 占用、
`pipeline` 已被 `multi_agent_pipeline` 占用，重名会在 `sys.modules` 里互相顶掉。

### 7.4 `--apply` 的语义

优化器自身始终 `update_source=False`（跑完源文件必还原）；是否把最优候选写回
`loop_agent/prompts/` 由 **gate 决策 + `--apply` 开关**共同决定。这保证：没过闸门的
候选永远不可能落到源文件，过了闸门也默认只进审计目录，写回是显式动作。

## 8 · 接入自有业务改哪里

1. **换 agent**：替换 `loop_agent/`，保留两个入口 —— `get_agent_async()`（评测器
   agent_module 模式，能捕获工具轨迹）和 `call_agent(query)->str`（优化器黑盒回调）。
   两者都必须每次调用重读 prompt 源文件。
2. **换数据**：`data/train.evalset.json` / `data/val.evalset.json` 换成你的业务 case。
   **验证集必须独立于训练集**（SDK 有同文件泄漏守卫，同源不同文件才是要靠本闭环的
   overfit 守卫兜住的情况）。
3. **换验收套件**：`data/eval_config.json` 的 `judge_model` 配真实模型
   （删掉 `provider_name: fake-judge`，配 `model_name`/`api_key`/`base_url`）。
4. **换优化配置**：`optimizer.json` 的 `reflection_lm` 同理；黑盒模式只能配响应类
   metric。
5. **调闸门**：`pipeline.json` 按业务风险改 —— 关键回归 case 加进 `protected_cases`，
   成本预算按真实模型定价设 `max_cost_usd`。

## 9 · 常见问题

**Q：为什么 no_effect 场景优化器报 `SUCCEEDED` 却被拒绝？**
`OptimizeResult.status=SUCCEEDED` 只表示优化循环正常结束（`finish_reason=
no_improvement`）。接受与否由 pipeline 的闸门决定 —— 这正是两层判定分离的意义。

**Q：过拟合守卫为什么用「train↑ 且 val↓」而不是单看 val？**
单看 val 下降只能说明候选不好；train 同时上升才是过拟合的指纹，报告据此给出
「调参集与训练集同源」这类可操作的诊断，而不是笼统的「变差了」。

**Q：trace 模式适合什么场景？**
线上已有轨迹日志、想先归因再决定要不要跑优化时：`--baseline-from-trace` 对
`data/trace_baseline.evalset.json`（`evalMode: "trace"`）直接评测归因，零 agent 执行。

**Q：报告里的成本为什么是 0？**
fake 模型不产生 token 费用。接真实模型后 `OptimizeResult.total_llm_cost` /
`total_token_usage` 会自动进入报告与 `cost_budget` 闸门。
