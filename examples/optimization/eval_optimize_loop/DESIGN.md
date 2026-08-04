# Eval + Optimize Loop — 架构设计

## 概述

本项目实现了 "评测 → 失败归因 → prompt 优化 → 回归验证 → 产物审计" 的自动化闭环。

输入一组评测集（evalset JSON）和优化器配置（optimizer.json），输出优化后的 prompt 和完整的审计报告。

## 架构

```
run_pipeline.py          # CLI 入口，编排 7 阶段流水线
├── pipeline/            # 统一包入口（from pipeline import ...）
│   ├── comparator.py    # trace 回放评测器（期望 vs 实际，分层规则）
│   ├── config.py        # 配置加载（optimizer.json + evalset JSON）
│   ├── baseline.py      # 基线评测（fake 回放 / SDK AgentEvaluator）
│   ├── attribution.py   # 失败归因（9 类根因分析）
│   ├── optimize.py      # 候选生成（三场景）+ 优化执行
│   ├── validate.py      # 候选重评分 + 验证集回归对比
│   ├── gate.py          # 多维度接受决策
│   ├── report.py        # JSON + Markdown 报告生成
│   └── tracing.py       # 审计追踪（seed/timing/cost/reproduce）
├── agent/
│   ├── agent.py         # build_call_agent() / run_agent()（优化目标）
│   ├── config.py        # Agent 配置
│   └── prompts.py       # 初始系统 prompt（优化目标）
├── data/
│   ├── train.evalset.json        # 训练评测集（34 cases，含 10 个 _fail）
│   ├── val.evalset.json          # 验证评测集（16 cases）
│   ├── large_train.evalset.json  # 压力评测集（50 cases）
│   ├── holdout.evalset.json      # hidden 集（12 cases）
│   ├── optimizer.json            # 优化器配置
│   └── prompts/system.md         # 被优化的 prompt 源文件
└── tests/               # 317 测试（6 维度）
```

## 7 阶段流水线

```
[1] config      → 加载 evalset JSON + optimizer.json
[2] baseline    → 在训练集和验证集上运行基线评测（trace 回放）
[3] attribution → 将失败 case 归因到 9 个根因类别
[4] optimize    → 按场景生成候选（fix_attributed/noop/overfit）
[5] validate    → 候选在验证集上重评，对比基线
[6] gate        → 6 维度决策：提升/关键case/新失败/过拟合/成本/负回归
[7] report      → 生成 JSON + Markdown 报告 + 审计追踪
```

## 两种执行模式

### Fake Mode（默认，无需 API Key）
- **trace 回放评测**：`comparator.py` 逐 case 比较 `conversation`（期望）
  与 `actual_conversation`（实际回放），判定通过/失败
- 三场景候选生成（`--scenario`）：可优化成功 / 优化无效 / 过拟合退化
- 确定性、可复现、零成本，单次运行 < 3 秒
- 适合 CI、本地验证、快速迭代

### Live Mode（需要 SDK + API Key）
- 调用 `AgentEvaluator.evaluate_eval_set()` 进行真实评测（trace 格式可离线）
- 调用 `AgentOptimizer.optimize()` 执行 GEPA reflective 优化（需 `call_agent`）
- 需要 `pip install trpc-agent-python[gepa]`
- SDK 不可用 / 配置不全时自动降级到离线 trace 回放，不崩溃

## 失败归因（9 类）

| 类别 | 描述 |
|------|------|
| `final_response_mismatch` | 最终回复与预期不匹配 |
| `tool_call_error` | 工具调用整体失败 |
| `wrong_tool_selected` | 选择了错误的工具 |
| `tool_parameter_error` | 工具参数错误 |
| `llm_rubric_not_met` | LLM rubric 评分未达标 |
| `knowledge_recall_insufficient` | 知识召回不足 |
| `format_not_as_required` | 输出格式不符合要求 |
| `missing_expected_output` | 缺少预期的输出内容 |
| `unknown` | 无法归类 |

归因准确率通过 `tests/test_gold_verdicts.py` 的黄金判定表锁定（≥90%），
每个失败 case 都带 `detail` + `evidence`（可解释原因）。

## 三候选场景

| 场景 | 行为 | 预期 gate |
|------|------|----------|
| `fix_attributed`（默认）| 候选修复归因的失败类别 | ACCEPT |
| `noop` | 候选无实质改动 | NEEDS_REVIEW |
| `overfit` | train 记住 + val 回归 | REJECT（过拟合）|

## Gate 决策（6 维度）

1. **提升阈值**：候选 pass_rate 相较于基线的最小绝对提升
2. **负回归拒绝**：候选 pass_rate 低于基线直接 REJECT
3. **关键 case 保护**：指定的关键 case 不能退化
4. **新增失败检测**：候选不能引入新的 hard fail
5. **过拟合检测**：候选在验证集新增失败 → REJECT（验收标准 #3）
6. **成本预算**：优化总成本不超过预算上限

决策结果：`accept` / `reject` / `needs_review`

## 审计追踪

每条 pipeline 运行记录：
- 随机种子（seed）
- 每个阶段的耗时（wall clock）
- 优化成本（USD）
- 输入文件 SHA-256 哈希
- 完整的复现命令

## 关键设计决策

1. **SDK 原生集成**：使用 `AgentEvaluator` 和 `AgentOptimizer` 的完整能力，而非自己重新实现
2. **Fake mode 优先**：默认模式不依赖外部 API，可离线运行
3. **模块化**：每个 pipeline 阶段是独立可测试的模块
4. **确定性可复现**：固定 seed 下结果一致，适合 CI 集成
5. **防御性设计**：每个阶段失败不影响其他阶段，错误记录在 audit trail 中
