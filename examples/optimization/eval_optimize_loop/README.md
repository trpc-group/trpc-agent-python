# Evaluation + Optimization 自动回归闭环

本示例把一次 prompt 优化从“分数变高了”升级为可审计的发布决策：先在 train/validation 上建立 baseline，再让真实 `AgentOptimizer` 与 GEPA 生成候选，随后对**每个去重候选**做独立 trace 复评、逐 case diff、失败归因和 Gate 判定，最后同时输出 JSON 与 Markdown 报告。默认模式不读取任何 API Key，也不访问网络。

## 一键运行

在仓库根目录执行：

```bash
pip install -e ".[eval,optimize]"
python examples/optimization/eval_optimize_loop/run_pipeline.py
```

无需设置 `OPENAI_API_KEY`、`TRPC_AGENT_API_KEY` 或其他模型凭据。指定输出目录：

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --output-dir /tmp/eval-optimize-loop
```

命令正常完成时输出 `decision=accepted` 与报告路径。默认不会覆盖源 prompt；只有显式传入 `--apply-if-accepted` 且所有必选 Gate 均通过，才会原子回写最终候选。

## 为什么不是“伪造优化”

离线 provider 通过框架公开的 `ModelRegistry` 注册三个模型名，并真实经过 SDK 的生产调用链：

```text
AgentOptimizer.optimize
  -> GepaReflectiveOptimizer
  -> gepa.optimize
  -> AgentEvaluator
  -> LlmAgent(offline/agent)
  -> 内置 LLMRubricResponseEvaluator(offline/judge)

reflection
  -> _OptimizeModelCallable
  -> LlmAgent(offline/reflector)
```

离线模型只替换远程推理这一处依赖；候选写入/恢复、GEPA 采样和筛选、rubric JSON 解析、评估聚合、回调及优化器 artifact 全部使用真实框架实现，没有 monkeypatch。外层回归会把每种 prompt 的确定性运行结果物化为 `actual_conversation`，再用 `AgentEvaluator` 的 trace-only 模式独立评分。

## 示例数据与三类候选

`data/train.evalset.json` 与 `data/val.evalset.json` 各有 3 个 case。它们不在实现代码中硬编码 case id，离线运行行为完全来自每个 case 的 `session_input.state.variant_traces`。

| 候选 | Train | Validation | 预期 Gate |
| --- | --- | --- | --- |
| `ineffective` | 无提升 | 无提升 | 拒绝：validation 增益不足 |
| `overfit` | 3/3 通过 | 关键 JSON case 从通过变失败 | 拒绝：hard fail、关键 case 回退、过拟合 |
| `robust` | 3/3 通过 | 3/3 通过 | 接受 |

六个 case 覆盖：最终回答不匹配、工具执行错误、工具参数错误、LLM rubric 失败、知识召回不足和格式失败。归因器从 actual/expected 工具轨迹、工具响应和 evaluator metric 中推导原因，不读取预埋的 `failure_category` 标签。格式归因优先使用逐 rubric 详情，其次使用 replay 证据，最后只解析请求中明确、非否定的 JSON、单行或 Markdown 约束；evaluator 没有产生有效 verdict 时单列 `evaluation_error`，不会伪装成语义失败。

## 输入

| 文件 | 用途 |
| --- | --- |
| `pipeline.json` | 路径清单、seed、bootstrap 次数和是否回写 |
| `optimizer.json` | 可直接被 SDK `load_optimize_config()` 校验的 GEPA 配置 |
| `regression.metrics.json` | 外层 trace 复评的回答、轨迹、rubric、知识召回指标 |
| `gate.json` | 增益、置信下界、hard fail、关键 case、过拟合和预算规则 |
| `agent/prompts/system.md` | baseline `TargetPrompt` 源文件 |
| `agent/prompts/candidates/*.md` | 离线 reflection model 的确定性候选 |
| `data/*.evalset.json` | train / validation case 与 replay trace |

## Gate 语义

每一项都在报告中记录 `passed / required / actual / expected / reason`。必选项做 AND：

1. `AgentOptimizer` 成功结束；
2. validation pass-rate 增益达到 `min_validation_gain`；
3. paired bootstrap 区间下界达到配置值；
4. 不新增 hard failure；
5. key case 不降分；
6. train 提升而 validation 下降时判定 overfit 并拒绝；趋势以 pass-rate 为主，持平时再比较 average score；
7. metric calls、token、耗时和成本均在预算内。

`forbid_new_hard_failures`、`key_cases_no_regression`、`reject_overfitting` 可以显式设为 `false`；报告仍保留检查结果，但 `required=false`，不会参与最终 AND。候选成本从每条 replay trace 的 `usage.cost`/`usage.cost_usd` 汇总并乘以 `num_runs`；任一值缺失时标记为 `unavailable`，成本 Gate fail-closed。仓库自带的离线 trace、judge 和 optimizer 均为本地确定性计算，因此样例成本被明确记录为“已测量的 0”，而不是把未知误写成 0。

## 统计与数据污染防护

- baseline 与 candidate 在同一 case 上配对，使用固定 seed 的 2,000 次 bootstrap，报告 validation pass-rate delta 的 95% 区间。
- 先过安全 Gate，再在合格候选中按 validation 质量、token 与 P95 latency 标记 Pareto 前沿。
- 运行前检查重复 id、train/validation 精确重复、去空白后的重复、相似度 ≥0.92 的近重复，以及 baseline/candidate prompt 中直接出现 validation reference answer；命中即 fail-closed。
- baseline、每个候选和每个 split 的 trace 均保存到 artifact 目录，可由 `AgentEvaluator` 重新播放。

## 输出

```text
runs/<timestamp>/
├── optimization_report.json       机器可读的完整闭环报告
├── optimization_report.md         人类可读决策报告
├── optimizer/                     AgentOptimizer 原始 result/round artifacts
└── traces/
    ├── baseline/{train,validation}.trace.evalset.json
    ├── ineffective/{train,validation}.trace.evalset.json
    ├── overfit/{train,validation}.trace.evalset.json
    └── robust/{train,validation}.trace.evalset.json
```

JSON 顶层包含 `baseline`、`candidates`、选中 `candidate`、`delta`、`gate`、`failure_attribution`、`optimizer`、`data_quality` 与 `audit`。每个候选保存完整 prompt、SHA-256、seed、来源轮次、逐 case delta、失败证据、metric/judge 调用数、token、P95 latency、耗时与成本。

仓库内的固定示例报告见 `sample_output/`。

## 验证

```bash
pytest -q tests/evaluation/test_eval_optimize_loop_example.py
```

测试覆盖无 Key 全链路、全部候选独立复评、六类失败归因与反误判、score-only 过拟合、paired bootstrap、Pareto、近重复数据拒绝、可配置保护项、成本 fail-closed 和逐候选资源审计。离线完整流程通常在数秒内完成，远低于 3 分钟验收上限。

## 接入真实业务

保留 `EvalOptimizePipeline.run()` 这个外部 interface，将 `offline.py` 的三个 adapter 替换为业务实现：`call_agent` 驱动真实 Agent，reflection/judge 的 `provider_name` 改为实际 provider，trace 物化器读取业务运行日志。`optimizer.json`、`TargetPrompt`、逐候选复评、diff、Gate 和报告模型无需改变。生产环境建议每次使用唯一 output directory；离线 registry 使用进程级固定 replay 配置，不支持在同一 Python 进程中并发启动两个 pipeline。
