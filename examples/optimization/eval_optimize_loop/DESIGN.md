# 方案设计说明

## 整体流程

Pipeline 读取训练集、验证集、Prompt 源文件、优化配置和 Gate 配置，先分别运行 baseline 评测，再对失败 case 做错误归因。随后优化器基于 baseline 结果生成候选 Prompt，Pipeline 使用候选 Prompt 重新评测训练集和验证集，并生成逐 case delta。最终 Gate 只根据候选复评结果和 delta 判断是否接受候选，报告同时输出 JSON 与 Markdown。

## 失败归因方法

错误归因由 `FailureAttributor` 负责，输入是标准化后的 case 结果、trace、metric details 和 evaluator metadata。规则优先识别工具调用名称或顺序错误、工具参数不匹配、最终回复不一致、LLM rubric 未达标、召回类指标失败和格式约束失败。每个失败 case 都会写入 `failure_analysis`，其中包含分类、置信度、解释和 evidence。无法稳定判断时归为 `unknown`，但仍保留原始失败线索，方便人工复核。

## 优化方法

优化目标是 `prompts/system.md` 与 `prompts/skill.md`。real 模式复用 `AgentOptimizer` 和 `TargetPrompt`，并固定 `update_source=False`，只生成候选 Prompt，不覆盖源文件。fake 模式使用确定性优化器，根据错误归因摘要追加修复指令，用于无 API Key 的可复现演示。候选 Prompt 必须重新跑评测，不能只依赖优化器内部的 aggregate 分数。

## 接受策略

Gate 以验证集为核心依据。默认策略要求验证集达到最小分数提升、不能新增失败、不能出现回归、关键 case 不允许退化、成本不能超过预算。每条规则都会生成 `GateRuleResult`，报告中保留规则名、是否通过、严重程度、说明和 evidence。最终决策为 `accept` 或 `reject`，并给出推荐动作。

## 防止过拟合策略

Pipeline 同时评测训练集和验证集，但 Gate 不因训练集提升而直接接受候选。当训练集提升达到阈值而验证集下降时，会标记为过拟合并拒绝。默认 fake 样例包含“训练集收益 + 验证集关键 case 退化”的情况，用于展示闭环如何避免把只优化训练样本的 Prompt 推向生产。

## 复现信息

`metadata` 负责保留最小复现信息：示例根目录、复现命令、输出路径和运行模式。这样既能定位这次运行的产物，又不会把输入文件 hash、配置脱敏快照和 Prompt diff 维护成单独一层。JSON 用于自动化消费，Markdown 用于人工复盘，两者都写入固定 `output/` 目录，便于比较和归档。
