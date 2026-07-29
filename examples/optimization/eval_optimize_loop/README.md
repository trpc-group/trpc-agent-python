# Evaluation + Optimization 自动闭环示例

## 示例目标

本示例演示如何在 `examples/optimization/eval_optimize_loop/` 中组合 `AgentEvaluator` 与 `AgentOptimizer`，完成 baseline 评测、错误归因、Prompt 优化、候选验证和 Gate 决策的完整闭环。实现全部保留在 example 内，不修改 `trpc_agent_sdk` 公共 API。

## 目录结构

```text
examples/optimization/eval_optimize_loop/
  README.md
  DESIGN.md
  gate.json
  optimizer.json
  output/
  prompts/
    system.md
    skill.md
  run_pipeline.py
  train.evalset.json
  val.evalset.json
  pipeline/
    __init__.py
    attribution.py
    delta.py
    gate.py
    optimization.py
    pipeline.py
    report.py
    types.py
  tests/
    conftest.py
    test_*.py
```

## 运行方式

先进入示例目录：

```bash
cd examples/optimization/eval_optimize_loop
```

fake 模式可无 API Key 运行完整流程：

```bash
python run_pipeline.py --mode fake
```

real 模式接入 `AgentEvaluator` 与 `AgentOptimizer`，需要可用模型凭证：

```bash
python run_pipeline.py --mode real
```

运行后固定生成：

- `output/optimization_report.json`
- `output/optimization_report.md`

仓库中提交的 `output/` 产物固定作为 fake 模式参考报告。如果运行 `--mode real` 覆盖了这两个文件，提交前请重新执行 `python run_pipeline.py --mode fake`。

## 输入与输出

输入包括：

- `train.evalset.json`
- `val.evalset.json`
- `optimizer.json`
- `gate.json`
- `prompts/system.md`
- `prompts/skill.md`

输出包括：

- `optimization_report.json`：给机器读取的结构化报告
- `optimization_report.md`：给开发者阅读的审计报告

## 报告内容

`optimization_report.json` 包含以下部分：

- `run`：运行时间、模式、随机种子
- `inputs`：输入文件相对路径
- `config`：gate 与 optimizer 的有效配置快照
- `baseline`：训练集与验证集 baseline 结果
- `failure_attribution`：baseline 与 candidate 的错误归因汇总
- `candidate`：候选 Prompt 的重新评测结果
- `delta`：baseline 与 candidate 的逐 case 差异
- `gate_decision`：接受或拒绝候选的规则结果
- `optimization`：优化轮次、原因、成本、耗时、Prompt 版本
- `metadata`：示例根目录、复现命令、输出路径

`optimization_report.md` 则按人类阅读顺序展示：

- 运行摘要
- Baseline 表现
- 错误归因汇总
- 优化过程
- 候选验证
- 逐 case delta
- Gate 决策
- 审计与复现

## fake 模式语义与目的

fake 模式用于在没有真实 API Key 时跑通完整闭环，目的不是模拟生产答案，而是稳定演示“评测 - 归因 - 优化 - 复评 - Gate”的业务链路。当前示例故意覆盖三类场景：

- 可优化成功：部分失败 case 被候选 Prompt 修复
- 优化无效：部分 case 仍保持失败
- 优化后退化：验证集关键 case 被候选 Prompt 破坏

因此 fake 报告默认会展示候选局部有收益，但最终仍因验证集退化或关键 case 退化而被拒绝。

## 优化目标

当前示例优化两个目标 Prompt：

- `system_prompt`
- `skill`

优化过程只生成候选版本，不覆盖源文件。Gate 只根据候选 Prompt 的独立复评结果做判断，不直接使用优化器自身的 aggregate 分数。

## 样例和场景

公开样例包含 6 条 case：

- 3 条训练 case
- 3 条验证 case

它们共同覆盖以下行为：

- baseline 通过
- baseline 失败
- 候选修复成功
- 候选保持失败
- 候选引入退化

## Gate 策略

默认 Gate 要求：

- 验证集必须有足够分数提升
- 不能新增验证集失败
- 不能出现验证集回归
- 关键 case 不允许退化
- 成本不能超预算
- 训练集提升但验证集下降时判定为过拟合

Gate 仅基于候选复评和 delta 做决策，不以优化器内部输出替代验证结果。

## real 模式说明

real 模式会调用现有 `AgentEvaluator` 与 `AgentOptimizer`。为了保证示例不回写源 Prompt，优化始终使用 `update_source=False`。如果缺少真实凭证，推荐使用 fake 模式。

## 测试与格式检查

```bash
pytest tests -q
yapf --diff pipeline/*.py run_pipeline.py tests/*.py
flake8 pipeline/*.py run_pipeline.py tests/*.py
```
