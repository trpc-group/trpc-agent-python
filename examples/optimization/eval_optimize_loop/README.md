# Evaluation + Optimization 自动回归闭环

本示例演示 issue #91 的完整治理流程：先用 `AgentEvaluator` 分别测量训练集与验证集 baseline，再调用候选优化器，随后对每轮 prompt 重新执行同一套评测，生成逐 case delta、失败归因、成本审计和最终 gate 决策。示例使用真实评测框架与确定性 fake model/fake optimizer，不需要 API Key，也不需要安装可选的 GEPA 依赖。

## 代码边界

可复用实现位于 `trpc_agent_sdk/evaluation/`：

- `_evaluation_optimization_config.py`：Pipeline 和 Gate 配置；
- `_evaluation_optimization_result.py`：报告与审计数据模型；
- `_evaluation_optimization_pipeline.py`：评测、优化、回归、Gate 和落盘流程。

本目录只负责演示如何调用这些公共能力。SDK 不导入本目录的
`fake_runtime.py`、样例数据或 prompt.

## 目录

```text
eval_optimize_loop/
├── prompts/system.md          # baseline TargetPrompt
├── train.evalset.json         # 3 条训练 case
├── val.evalset.json           # 3 条验证 case
├── optimizer.json             # evaluator、optimizer 与 pipeline gate 配置
├── fake_runtime.py            # 确定性 fake model / 两轮候选生成器
├── run_pipeline.py            # 入口脚本
├── sample_output/             # 已提交的完整离线审计样例
│   ├── optimization_report.json
│   └── optimization_report.md
└── DESIGN.md                  # 300–500 字方案设计说明
```

六条 case 覆盖三类结果：

- `train_json_tier`、`train_refund_router`、`val_json_invoice` 分别验证
  CRM JSON、退款队列和 ERP JSON 规则，可被候选修复；
- `train_unknown_codename`、`val_live_inventory` 分别缺少知识库记录和仓库
  连接器结果，在候选下仍失败，体现 prompt 优化不能替代外部数据能力；
- 第一轮 overfit prompt 会让 `val_system_prompt_safety` 从通过变为失败，必须被 gate 拒绝。

第二轮 balanced prompt 保留关键安全行为，并让验证集平均分和通过率各提升约 `0.3333`，因此被接受。示例配置的 `update_source=false`，运行后源 prompt 不会变化。
baseline prompt 有意保留真实但不完整的业务规则：包含事实边界与保密约束，但未定义
机器可读输出契约和退款队列标签。两轮候选添加的都是可读业务指令，不使用隐藏开关或
特殊 profile 标记。

## 运行

从仓库根目录执行：

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py
```

也可以指定独立输出目录：

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --output-dir examples/optimization/eval_optimize_loop/runs/manual
```

终端只输出最终 `ACCEPT`/`REJECT` 和两个报告路径。默认新建
`examples/optimization/eval_optimize_loop/runs/latest/`，其中还包括：

- `baseline_prompts/`：运行前 prompt 快照；
- `candidates/round_NNN/`：每轮候选全文；
- `rounds/round_NNN.json`：该候选的训练/验证结果、delta 与 gate；
- `optimizer_result.json`：底层优化器原始结果；
- `config.snapshot.json`：脱敏后的完整实验配置；
- `evaluation_config.snapshot.json`：本次 `AgentEvaluator` 使用的指标配置。

示例在 `optimizer.json` 中配置 `pipeline.report_language="zh-CN"`，因此每次运行
都会生成中文 `optimization_report.md`。业务项目可改为 `"en"`；机器读取的
`optimization_report.json` 字段名和 schema 不随报告语言变化。

## 接入真实 AgentOptimizer

业务接入时保留 `TargetPrompt`、train/val evalset 和 `call_agent`，调用
`EvaluationOptimizationPipeline.run(...)` 时不传 `optimizer_runner`，闭环会使用
`AgentOptimizer.optimize`。真实模式需要安装项目的 `optimize` 可选依赖并配置模型：

```bash
pip install -e ".[optimize]"
```

只有 `pipeline.update_source=true` 且最终 gate 接受时才会写回 prompt；底层优化器的内部
`accepted` 不等同于生产接受。`max_total_cost_usd` 的评测部分由
`evaluation_case_cost_usd × case-run 数`估算，优化器本身使用
`OptimizeResult.total_llm_cost`。

## Trace mode

将 train/val case 写成标准 `eval_mode: "trace"` 与 `actual_conversation` 后，把
`pipeline.mode` 改为 `trace`，即可在没有 `call_agent` 的情况下运行回归。由于静态 trace
不会随 prompt 改变，trace 模式需要注入一个能够提出候选的 `optimizer_runner`；所有候选
仍会经过相同的归因、逐 case delta、gate 和审计路径。此模式适合验证历史轨迹和 gate
逻辑，fake model 模式适合验证 prompt 改动造成的行为变化。
