# 方案设计说明

闭环将训练集与验证集严格分离：baseline 逐条保存分数、通过状态、轨迹和成本，再依据证据归因。归因依次检查工具、参数、知识召回、格式、rubric 与最终回复，保证每个失败样本至少产生一个原因。候选 prompt 只使用训练失败簇生成，禁止读取验证答案。

候选重新运行全部验证样本，逐条标记新增通过、新增失败、提升、下降或不变。gate 同时检查总分提升、新增 hard fail、关键 case、单 case 下降和成本；任一条件不满足即拒绝，因此训练提升但验证退化的候选无法回写。默认 `update_source=false`，保留人工审批点。

实验落盘 baseline、candidate、逐 case delta、归因统计、候选 prompt、每轮分数、成本、耗时、随机种子、配置及理由，并生成 JSON 与 Markdown 报告。trace mode 使用固定轨迹，无 API Key 也可复现；生产可用 AgentEvaluator 替换 fake model、用 AgentOptimizer 生成 TargetPrompt，但比较、gate 与审计保持独立，避免优化器自评。未知失败归为最终回复不匹配而非忽略。
