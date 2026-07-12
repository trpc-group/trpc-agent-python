# 方案设计

本方案将评测优化闭环的核心从“根据失败文本猜测原因”改为“通过同一评测器验证最小行为反事实”。系统先检查训练集、验证集、提示词和预算契约，并将样例标记为 trusted、suspect 或 invalid。只有 trusted 且具有修复证据的失败能够驱动优化。

对于失败轨迹，系统深拷贝实际对话，分别替换最终回复、工具名称、工具参数或受限组合，保持预期对话不变，再调用 `AgentEvaluator.get_executer()` 评分。单一干预修复失败时形成强归因；只有组合有效时判定为复合失败。数据、评测器和基础设施异常不进入优化摘要。若局部替换与原工具响应不一致，证据会降低置信度。

归因结果映射到 router、skill 和 system 三类 `TargetPrompt`。真实优化入口固定使用 `AgentOptimizer.optimize(update_source=False)`，候选必须在完整验证集重新评测，新增退化再次执行反事实诊断。Gate 采用 all-must-pass，检查验证增益、可信子集、hard fail、关键样例、严重度、成本、耗时和证据。只有 gate 接受且显式传入 `--apply` 时才调用 `TargetPrompt.write_all()`，并记录写前写后哈希。报告保存轨迹证据、候选差异、拒绝原因、随机种子及复现命令。

## 技术附录

- 反事实归因：结论来自真实 metric delta，不依赖 case ID、failure reason 或人工标签。
- Prompt actionability：只有 agent behavior failure 能够选择优化表面。
- Gate：关键检查必须全部通过，证据不足时以 `NEEDS_REVIEW` 拒绝。
- 防过拟合：训练失败驱动优化，完整 validation 决策，新退化独立诊断。
- 审计：记录输入和提示词哈希、seed、耗时、成本、候选及每项 gate 证据。
- 限制：局部轨迹替换可能形成现实中不可执行的状态，因此显式记录一致性并降低置信度。
