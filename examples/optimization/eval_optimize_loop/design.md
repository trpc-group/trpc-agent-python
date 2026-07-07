# Design note

本示例把“评测、归因、优化、验证、准入、审计”串成可复现闭环。入口脚本先把 train 和 validation 引用转成 trace evalset，用 `AgentEvaluator` 评估 baseline，再按 case 汇总失败原因。归因不只看总分，而是同时检查最终回答、工具轨迹、工具参数和 fake judge rubric：文本不一致归为 response mismatch，工具名或调用顺序错误归为 tool call error，参数差异归为 tool argument error，JSON 契约失败归为 format noncompliance，缺少私有知识且提示词无法补足时归为 knowledge recall gap。

接受策略由 `optimizer.json` 的 gate 控制。候选必须在验证集达到最小分数提升，不能引入新的 hard failure，关键 case 不能退化，并且成本必须在预算内；任一条件失败都会拒绝。该样例故意让候选修复训练集和一个验证集格式问题，同时使 `val_critical_discount` 与 `val_stable_refund` 退化，因此最终 gate 应拒绝，用来演示“训练收益不等于可发布”。

防过拟合策略包括 train/validation 分离、关键验证 case 白名单、逐 case delta 对比、记录新增失败和退化分数，并把不可由 prompt 解决的 knowledge gap 与可优化格式/参数问题分开处理。产物审计方面，报告保存 baseline、candidate、每轮优化输入、候选 prompt、seed、成本、模型调用数、门禁检查、失败归因统计和生成的 trace evalset 路径；默认 fake backend 无需密钥，便于 CI 和评审者复跑比对。
