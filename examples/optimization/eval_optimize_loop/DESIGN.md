# Evaluation + Optimization 实现设计

> [`Pipeline 源码`](../../../trpc_agent_sdk/evaluation/_evaluation_optimization_pipeline.py) ·
> [`配置模型`](../../../trpc_agent_sdk/evaluation/_evaluation_optimization_config.py) ·
> [`报告模型`](../../../trpc_agent_sdk/evaluation/_evaluation_optimization_result.py)

```text
run
├─ _evaluate → _build_snapshot → _build_case_evaluation
├─ _candidate_specs → _compare_snapshots
└─ _apply_gate → _select_candidate → _persist_artifacts
```

## 1. 基线与分数聚合

`run()` 校验输入、加载配置，并用 `TargetPrompt.read_all()` 保存 baseline。

`_evaluate()` 将 evalset 解析为 `EvalSet`，调用
`AgentEvaluator.evaluate_eval_set()` 得到每个 case 的多次运行结果。

`_build_case_evaluation()` 对同名 metric 取均值，所有 run 均通过时 case 才通过；
case 分数为各 metric 均值。

`_build_snapshot()` 再计算 case 均分、通过数占比和
metric breakdown，形成 train/validation 快照。

## 2. 失败归因与轨迹

`_classify_failure()` 先检查 metric 名和裁判 reason，再调用
`_classify_tool_failure()` 比较工具序列及参数；`_has_structured_format_failure()`
通过 JSON 解析区分格式错误与普通回复错误。

配置可用 `failure_category_overrides` 覆盖领域分类；无信号时写入 `unknown_failure`。
`_trace_from_run()` 保存 invocation 索引、实际/预期回复和工具调用，保证每个失败
case 都有原因和定位信息。

## 3. 候选回归与状态恢复

`_candidate_specs()` 校验候选字段与 `TargetPrompt` 一致，用排序后的 JSON 指纹去重，
补入 optimizer 的 best prompt，并受 `max_candidates` 限制。

每个候选通过
`TargetPrompt.write_all()` 临时生效，随后重跑 train/validation；
`finally` 始终恢复 baseline。`_compare_snapshots()` 以
`(eval_set_id, case_id)` 对齐前后结果，输出新增通过、新增失败、提升、下降和不变。

## 4. Gate、选择与审计

`_apply_gate()` 为优化器状态、验证分/通过率、hard fail、关键 case、回归数、过拟合
和成本分别生成 `GateCheck`。过拟合条件为训练分提升，且验证提升未达阈值或存在
退化 case；任一启用项失败即拒绝。

`_select_candidate()` 优先从 Gate 通过者中按验证分、通过率和训练分选择。`_persist_artifacts()` 保存 baseline、候选与逐轮 JSON；
`OptimizationReport.write()` 用临时文件加 `os.replace()` 原子写入总报告。

`_file_artifact()` 记录 SHA-256，`_redact()` 脱敏配置；只有最终接受且
`update_source=true` 才写回源 prompt。
