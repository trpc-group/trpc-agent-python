# Evaluation + Optimization 闭环设计

## 方案说明

本示例复用 `AgentEvaluator`、`AgentOptimizer` 和 `TargetPrompt`，不修改生产
源码。Pipeline 先分别评测 train/validation，保存每条 case 的 metric、状态、
失败原因和关键轨迹；再按执行异常、回复不匹配、工具名称、工具参数、rubric、
知识召回和格式问题进行确定性归因。优化阶段只修改 working copy，候选完成
train/validation 回放后，按 case 输出新增通过、新增失败、分数提升、分数下降
和 unchanged。Gate 检查验证集提升阈值、无新增 hard fail、critical case 不退化、
验证集退化和成本/耗时预算；训练提升而验证退化直接判定过拟合。fake-model、
fake-judge 和 trace mode 使用同一比较与 gate 链路，保证无 API Key 也能复现。
报告 JSON/Markdown 保存输入 hash、候选、逐 case delta、归因、成本、耗时和理由；
默认不回写源 prompt，只有显式 `--write-back` 且
gate 接受时才写回。

## 阶段与 Review

- A：模型、输入校验和 evaluator 适配；Review A 检查结果保留、泄漏和边界。
- B：优化、归因、逐 case diff、gate 和 working-copy；Review B 检查过拟合、
  hard fail、成本和 prompt 恢复。
- C：CLI、fake/trace、报告和样例；Review C 对照 Issue #91 核对交付物。
- D：目标测试、覆盖率、flake8、函数复杂度和最终 diff；Review D 做交付审查。

## 主要文件

实现：`loop/models.py`、`loop/evaluation.py`、`loop/analysis.py`、
`loop/pipeline.py`、`loop/reporting.py`、`agent/agent.py`、`run_pipeline.py`。

资源：`data/train.evalset.json`、`data/val.evalset.json`、
`data/fake_trace.json`、`optimizer.json`、`gate.json`、
`optimization_report.json`、`README.md`。

`optimization_report.json` 是 Issue #91 要求的示例输出，不是稳定契约。
其中时间戳、Git SHA、Python 版本和耗时仅展示审计字段，实际运行由 pipeline
在输出目录重新生成，测试不依赖这些环境相关值。

测试：`tests/evaluation/test_eval_optimize_loop_*.py`。

## 验收

```bash
uv run pytest tests/evaluation/test_eval_optimize_loop_*.py \
  --cov=examples.optimization.eval_optimize_loop --cov-fail-under=90
uv run flake8 --max-complexity=15 --max-line-length=120 \
  examples/optimization/eval_optimize_loop
uv run python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --fake-model --fake-judge
```
