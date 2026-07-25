# Evaluation + Optimization 自动闭环

本示例把 `AgentEvaluator` 与 `AgentOptimizer`（或可插拔的离线等价后端）
编排为可复现的“基线评测 → 失败归因 → prompt 候选 → 验证集回归 →
接受决策 → 审计落盘”闭环。默认配置不需要 API Key，并刻意包含一轮可接受
优化和一轮训练集提升、验证集退化的过拟合优化。

## 快速运行

在仓库根目录执行：

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --run-id offline-demo
```

默认结果写入 `runs/<timestamp>/`。指定固定目录便于 CI 收集：

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --run-id ci-regression \
  --output-dir /tmp/eval-optimize-loop
```

fake model、确定性 fake judge 和 trace mode 下只执行 18 次本地调用，通常在
数秒内完成，远低于 3 分钟验收限制。

## 输入

| 文件 | 作用 |
| --- | --- |
| `train.evalset.json` | 3 条训练 case，供失败分析和候选生成 |
| `val.evalset.json` | 3 条隔离验证 case，只用于回归和 gate |
| `optimizer.json` | 评测指标、优化后端、随机种子、成本和接受策略 |
| `prompts/system.md` | baseline `TargetPrompt` 源文件 |

公开 case 覆盖三类结果：

| 类型 | Case |
| --- | --- |
| 可优化成功 | `train_tool_weather`、`train_format_invoice`、`val_router_calendar` |
| 优化无效 | `train_knowledge_unavailable` |
| 优化后退化 | `val_stable_math`、`val_critical_safety`（过拟合候选） |

训练集与验证集的 case ID 必须完全隔离，否则 pipeline 启动时 fail-fast。
非法 `execution_mode`，或 `agent_optimizer` 后端缺少 `optimize` 配置，也会在
任何审计文件写入前被拒绝。

## 执行路径

默认 `pipeline.execution_mode=trace`。`FakePromptModel` 读取当前候选 prompt，
生成确定性响应，pipeline 将响应写成标准 `actualConversation`，随后由
`AgentEvaluator` 的 trace mode 完成打分。`final_response_avg_score` 的 exact
matcher 充当离线 fake judge，原始 trace 和结构化 evaluator 结果都会落盘。

将 `execution_mode` 改为 `call_agent` 可直接验证
`AgentEvaluator.get_executer(call_agent=...)` 路径。将
`optimizer_backend` 改为 `agent_optimizer` 并配置
`TRPC_AGENT_MODEL_NAME`、`TRPC_AGENT_BASE_URL`、`TRPC_AGENT_API_KEY` 后，
pipeline 会调用：

```python
await AgentOptimizer.optimize(
    ...,
    update_source=False,
)
```

`AgentOptimizer` 只负责搜索候选；最终是否回写仍由独立验证 gate 决定。

## 接受策略

`optimizer.json > pipeline.gate` 同时检查：

1. 验证集总分和 pass rate 达到最小提升；
2. 不允许新增 hard fail；
3. 关键 case 不允许分数退化；
4. 新增普通失败数不超过上限；
5. 训练增益与验证增益之差不超过过拟合阈值；
6. 候选优化和回归成本不超过预算。

所有检查按 AND 语义执行并默认 fail closed；成本预算缺失、为负数或非有限值
时会直接拒绝候选。示例中
`balanced_candidate` 通过，`overfit_candidate` 因验证分下降、关键安全
case 退化和训练/验证增益差过大而被拒绝。

## 输出

每次运行会生成：

```text
<output_dir>/
├── optimization_report.json
├── optimization_report.md
├── audit/
│   ├── evaluation_config.json
│   └── resolved_optimizer_config.json
├── candidates/<round>_<candidate>/system_prompt.md
└── evaluations/<baseline-or-candidate>/<dataset>/
    ├── evaluate_result.json
    ├── <dataset>.trace.evalset.json
    └── raw/
```

报告包含 baseline/candidate 分数、逐 case delta、失败归因、每轮 prompt、
gate 检查和理由、调用成本、耗时、随机种子、输入路径及 SHA-256。仓库中的
`optimization_report.json` 是默认 fake/trace 配置的示例输出。未显式指定
`--run-id` 时使用 UTC 微秒时间和唯一后缀；JSON 与 Markdown 报告均原子落盘。

默认不会修改源 prompt。只有显式传入 `--update-source` 且所选候选通过全部
gate 时才原子回写；候选被拒绝或运行异常时源文件恢复为 baseline。

方案细节见 [DESIGN.md](DESIGN.md)。
