# 方案设计说明

## 失败归因方法

失败归因系统基于 `EvalCaseResult.overall_eval_metric_results` 逐 metric 检查失败原因。对确定性 metric（`final_response_avg_score`、`tool_trajectory_avg_score`），归因为 `final_response_mismatch`（回复文本不含期望内容）和 `tool_trajectory_mismatch`（工具调用与期望不一致）。当两个 metric 同时失败时归因为 `both_metrics_failed`，表示行为层面出现根本偏差。对 LLM 评分 metric（`llm_rubric_response`、`llm_rubric_knowledge_recall`），额外支持 `llm_rubric_fail` 和 `knowledge_recall_insufficient` 分类。所有失败 case 按类别聚类，生成 `FailureAttributionReport`，标识主要失败模式（如 70% 失败为 final_response_mismatch），为优化方向提供数据支持。每个失败 case 至少给出一个可解释原因，通过 metric 名称和阈值对比自动生成。

## 接受策略

接受门控采用多检查 AND 逻辑——任一检查失败即拒绝候选 prompt。检查项包括：(1) 验证集 pass rate 提升 ≥ 指定阈值；(2) 无新增 hard failure（baseline 通过的 case 候选不能失败）；(3) 回归数量不超过上限；(4) 关键 case 必须通过；(5) 总成本不超过预算。此设计优先保证安全性：一个在训练集上大幅提升但导致验证集关键 case 退化的候选会被拒绝。

## 防过拟合策略

防过拟合采用三层机制：首先，优化器使用训练集进行 prompt 搜索，但接受门控基于验证集独立评估，两者严格分离。其次，`no_new_hard_failures` 规则直接检测过拟合信号——训练集提升但验证集新增失败。第三，逐 case delta 报告透明展示每个 case 的分数变化，使得"训练集提升但验证集上部分 case 退化"的典型过拟合模式一目了然。示例数据中 train_001 和 val_001 优化后通过、val_003 意外退化的场景，正是一个被门控正确拒绝的过拟合案例。

## 产物审计

每次运行生成两份报告：`optimization_report.json`（机器可解析，适合 diff 和自动化分析）和 `optimization_report.md`（人类可读，适合非技术干系人）。审计轨迹包含：baseline 逐 case 逐 metric 评分、失败归因聚类、优化轮次记录与候选 prompt、候选验证评分与逐 case delta、门控决策及所有检查结果、运行耗时和成本。JSON 报告使用确定性键序和一致结构，支持跨运行 diff 对比。所有数据落盘到指定 output 目录，确保每次运行完全可复现。
