# eval_optimize_loop · 评测 + 优化自动闭环

把 `AgentEvaluator`（评测）与 `AgentOptimizer`（优化）串成一条可复现、可审计的闭环：

```
Baseline 评测 → 失败归因 → prompt 优化 → 候选验证(逐 case delta) → 接受门控 → 审计落盘
```

它回答的不是"能不能跑一次优化"，而是"这次优化**是否真的值得接受**"——是否提升、
是否牺牲其他指标、是否过拟合、是否值得回写源 prompt。

## 快速开始

```bash
# 离线 fake 模式：无需 API Key，确定性、可复现，秒级完成（默认）
python run_pipeline.py

# 真实模式：需要 TRPC_AGENT_API_KEY / TRPC_AGENT_BASE_URL / TRPC_AGENT_MODEL_NAME
python run_pipeline.py --mode real
```

产物写入 `runs/<timestamp>/`，含 `optimization_report.json` 与 `optimization_report.md`。
退出码：**0 = 接受候选**，**2 = 拒绝候选**（拒绝是有效负决策，便于 CI 判定）。

仓库已提交两份示例输出：
- 规范样例（fake 模式，确定性）：[optimization_report.json](optimization_report.json) /
  [optimization_report.md](optimization_report.md) —— 完整复现"成功/无效/退化"三场景与过拟合拒绝。
- 真实链路样例（real 模式，实跑于一个 OpenAI 兼容端点）：
  [samples/real_sample.optimization_report.md](samples/real_sample.optimization_report.md)。
  该次真实模型在验证集 baseline 已 3/3 满分（真实 LLM 不受 fake 的 `@cap` 能力标记约束，
  乘法本就会做），故 GEPA 0 轮无可优化，门控据此以"无提升"正确拒绝——这也是一种有效负决策。

## 双后端设计（为什么有 fake）

issue 要求 "没有真实 API Key 时也能跑通核心流程" 且 "fake 模式 ≤ 3 分钟"。因此
pipeline 是**双后端**：编排、评测、门控、报告四层两档**完全共用真实代码**，fake 只替换
两个要花钱/联网的点。

| 阶段 | fake（默认·离线） | real（配 Key） |
|---|---|---|
| Agent 推理 | `agent/fake_backend.py` 确定性求解 | `agent/orchestrator.py` 真实多 agent + `LlmAgent` |
| 评测打分 | 真实 `AgentEvaluator` + text-contains | 同左 |
| 失败归因 | 确定性规则桩 | 纯 LLM 裁判 |
| 优化 | 脚本化候选 | 真实 `AgentOptimizer`(GEPA) |

fake 后端从 prompt 文件里的 `<!-- @cap: X -->` 能力标记决定行为，于是"改 prompt"被
映射成"改能力集合"，让每条 case 的 pass/fail 随候选确定性翻转——这正是稳定复现三类
场景所需的可控信号。

**无 Key 跑通的三种途径**（issue 要求 fake judge / fake model / trace mode）：
- **fake model**：默认档，`call_agent` 换成确定性求解器（本示例采用）。
- **fake judge**：评测 metric 默认用 `final_response` 文本匹配，不调用任何裁判模型；
  若改用 LLM-rubric 指标，把判分入口替换为规则桩即可（`pipeline/attribution.py` 的
  `classify_fake` 即是失败归因的 fake judge 实现）。
- **trace mode**：evalset case 可携带预录 `intermediate_data`，`AgentEvaluator` 以
  trace 直接评分而不驱动 agent（SDK 原生支持，无需模型）。

## 优化目标（三字段 TargetPrompt）

对应 `agent/prompts/` 三个文件，`round_robin` 每轮只改一个便于归因：

- `router` — [router.md](agent/prompts/router.md)：题型分流
- `system_prompt` — [system.md](agent/prompts/system.md)：输出格式约束
- `skill` — [skill.md](agent/prompts/skill.md)：解题题型能力

## 样例 case 与三类场景

6 条 case（3 训练 / 3 验证，见 [data/](data/)），覆盖 issue 要求的三类：

| 场景 | case | baseline → candidate |
|---|---|---|
| 可优化成功 | `train_mul_car` / `val_mul_box` | FAIL → PASS（学会乘法） |
| 优化无效 | `train_discount_shirt` | FAIL → FAIL（折扣仍不会） |
| 优化后退化 | `val_add_class` | PASS → FAIL（大数加法被过拟合规则误算） |

候选在训练集 +0.33 却在验证集出现新增失败 → **门控拒绝**，正是"训练涨、验证退"的
过拟合必须挡下的情形。

## 失败归因（六类）

最终回复不匹配 / 工具调用错误 / 参数错误 / LLM rubric 不达标 / 知识召回不足 /
格式不符合要求。每条失败至少给出一条可解释原因，并聚类成类别计数（见报告第 2 节）。

## 接受门控（可配置，[config.json](config.json)）

```json
"gate": {
  "min_val_score_delta": 0.05,      // 验证集均分提升需 ≥ 此值
  "forbid_new_hard_fail": true,     // 不得新增 hard fail（原通过转失败）
  "key_case_ids": ["val_add_class"],// 关键 case 不得退化
  "cost_budget_usd": 1.0            // 成本预算
}
```

任一规则不过即拒绝。

## 目录结构

```
eval_optimize_loop/
├── run_pipeline.py          # 入口：六阶段编排
├── config.json              # gate + seed
├── optimizer.json           # AgentOptimizer(GEPA) 配置（real 模式）
├── eval_metrics.json        # 共享评测 metric（final_response contains）
├── DESIGN.md                # 300–500 字方案说明
├── optimization_report.json # 示例输出（fake 模式）
├── optimization_report.md
├── data/                    # train.evalset.json / val.evalset.json（6 条）
├── agent/
│   ├── orchestrator.py      # real 档：router→solver(system+skill)
│   ├── fake_backend.py      # fake 档：确定性求解器
│   ├── config.py            # real 档模型配置（读环境变量）
│   └── prompts/             # router.md / system.md / skill.md
└── pipeline/
    ├── evaluate.py          # AgentEvaluator → 结构化逐 case
    ├── attribution.py       # 六类失败归因（LLM / 规则）
    ├── optimize.py          # AgentOptimizer 包装 + 脚本化候选
    ├── gate.py              # 逐 case delta + 接受门控
    └── report.py            # optimization_report.{json,md}
```
