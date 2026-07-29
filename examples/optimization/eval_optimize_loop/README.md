# Evaluation + Optimization 闭环示例

本示例把 `AgentEvaluator` 与 `AgentOptimizer` 串成可复现的
Champion–Challenger 流程：冻结输入、评测基线、归因失败、生成候选、
重新评测、执行 Gate，并保存 JSON/Markdown 报告。默认 dry-run；只有
显式传入 `--apply` 且全部 Gate 通过时才更新源 Prompt。

## 无 API Key 的管线回归

```powershell
python .\examples\optimization\eval_optimize_loop\pipeline.py --mode fake --scenario success
python .\examples\optimization\eval_optimize_loop\pipeline.py --mode fake --scenario no_effect
python .\examples\optimization\eval_optimize_loop\pipeline.py --mode fake --scenario overfit
```

| 场景 | 明文 `FAKE_CONTROLS` | 预期结果 |
|---|---|---|
| `success` | `ADD_STEPS=true` | train/val 均提升，ACCEPT |
| `no_effect` | 两项均为 `false` | 无有效提升，REJECT G1 |
| `overfit` | `MEMORIZE_TRAIN=true` | train 提升、val 退化，REJECT G2 |

Fake 模式根据 Prompt 内的公开控制项生成 trace，再交给真实
`AgentEvaluator` 评分；它只用于确定性管线回归，不证明隐藏样本泛化能力，
也不替代原生优化器。

## 原生 optimize

```powershell
$env:TRPC_AGENT_API_KEY="..."
$env:TRPC_AGENT_BASE_URL="https://..."
$env:TRPC_AGENT_MODEL_NAME="..."
python .\examples\optimization\eval_optimize_loop\pipeline.py --mode optimize --optimizer-config optimizer.json
```

该路径使用真实模型回调并实际调用
`AgentOptimizer.optimize(..., update_source=False)`。Optimizer 仅看到训练集；
返回的 `best_prompts["system"]` 作为 Challenger，再用同一 train/val 数据和
Gate 做回归。缺少配置、优化异常或未产出 Candidate 时仍会生成
`OPTIMIZER_FAILURE + G6` 的 REJECT 报告。模型端未返回可信 token/费用时，
报告写 `cost_status="unavailable"`、数值为 `null`，并禁止自动写回。

## Gate 与写回

G1 验证集最小提升；G2 训练涨而验证跌；G3 不新增高风险失败；G4 protected
case 不退化；G5 slice 退化不超阈值；G6 成本证据完整且不超预算；G7
过滤 epsilon 内的微小变化。阈值见 `run.json`。

```powershell
# 只有 ACCEPT 才会通过 TargetPrompt 原子写回
python .\examples\optimization\eval_optimize_loop\pipeline.py --mode fake --scenario success --apply
```

REJECT 加 `--apply` 返回退出码 2，Champion 保持不变。评测临时切换和测试恢复
也统一使用 `TargetPrompt.read_all/write_all`。

## 数据、证据与产物

- `data/train.evalset.json`、`data/val.evalset.json`：各 3 条，eval_id 互斥。
- `data/attribution_holdout.json`：公开标注证据集，不是官方隐藏集。
- `optimization_report.json/.md`：最新决策及逐 case 证据。
- `optimization_report.example.json`：已脱敏的报告结构示例。
- `runs/<UTC时间>-<随机后缀>/`：冻结清单、Prompt 快照、完整 evaluator
  结果、优化轮次、调用审计与报告；该目录被 Git 忽略。

逐 case 报告保存 actual/expected response、metric reason、tool use/response、
参数差异和 trace 引用，并区分 agent-quality、infrastructure failure 与
`insufficient_evidence`。官方隐藏集未提供，因此不声称满足隐藏样本准确率；
公开归因 holdout 的实际统计由测试计算。

## 验证

```powershell
python -m pytest .\examples\optimization\eval_optimize_loop\tests -q
python -m compileall -q .\examples\optimization\eval_optimize_loop
```

测试包含三种 fake 场景、mock native optimizer 完整闭环、真实 evaluator
工具/参数证据、成本不可用拒绝 apply、Prompt 恢复、Gate 和报告 schema。
