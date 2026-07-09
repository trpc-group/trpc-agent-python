# Eval + Optimize Loop — 架构设计

## 概述

本项目实现了 "评测 → 失败归因 → prompt 优化 → 回归验证 → 产物审计" 的自动化闭环。

输入一组评测集（evalset JSON）和优化器配置（optimizer.json），输出优化后的 prompt 和完整的审计报告。

## 架构

```
run_pipeline.py          # CLI 入口，编排 7 阶段流水线
├── pipeline/
│   ├── config.py        # 配置加载（optimizer.json + evalset JSON）
│   ├── baseline.py      # 基线评测（fake mode / SDK AgentEvaluator）
│   ├── attribution.py   # 失败归因（8 类根因分析）
│   ├── optimize.py      # 优化执行（fake mode / GEPA reflective）
│   ├── validate.py      # 验证集回归对比
│   ├── gate.py          # 多维度接受决策
│   ├── report.py        # JSON + Markdown 报告生成
│   └── tracing.py       # 审计追踪（seed/timing/cost/reproduce）
├── agent/
│   ├── agent.py         # 被评测的 calculator agent
│   ├── config.py        # Agent 配置
│   └── prompts.py       # 初始系统 prompt（优化目标）
├── data/
│   ├── train.evalset.json   # 训练评测集
│   ├── val.evalset.json     # 验证评测集
│   ├── optimizer.json       # 优化器配置
│   └── prompts/
│       └── system.md        # 被优化的 prompt 源文件
└── tests/                   # 129+ 测试
```

## 7 阶段流水线

```
[1] config      → 加载 evalset JSON + optimizer.json
[2] baseline    → 在训练集和验证集上运行基线评测
[3] attribution → 将失败 case 归因到 8 个根因类别
[4] optimize    → 执行 GEPA 优化（fake 或 live 模式）
[5] validate    → 对比基线 vs 候选在验证集上的表现
[6] gate        → 5 维度决策：提升/关键case/新失败/成本/过拟合
[7] report      → 生成 JSON + Markdown 报告 + 审计追踪
```

## 两种执行模式

### Fake Mode（默认，无需 API Key）
- 加载 evalset JSON，基于已有数据模拟评测结果
- 基于归因结果模拟 GEPA 优化
- 确定性、可复现、零成本
- 适合 CI、本地验证、快速迭代

### Live Mode（需要 SDK + API Key）
- 调用 `AgentEvaluator.evaluate_eval_set()` 进行真实评测
- 调用 `AgentOptimizer.optimize()` 执行 GEPA reflective 优化
- 需要 `pip install trpc-agent-python[gepa]`

## 失败归因（8 类）

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

## Gate 决策（5 维度）

1. **提升阈值**：候选 pass_rate 相较于基线的最小绝对提升
2. **关键 case 保护**：指定的关键 case 不能退化
3. **新增失败检测**：候选不能引入新的 hard fail
4. **成本预算**：优化总成本不超过预算上限
5. **过拟合检测**：验证集不能退化（由 validate 阶段判断）

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
