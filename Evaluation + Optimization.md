背景和价值
tRPC-Agent 已提供 AgentEvaluator 和 AgentOptimizer 两类关键能力：前者负责把 Agent 行为固化成可回归的评测信号，后者基于评测结果自动搜索更优 prompt、skill 描述或 sub-agent 指令。真实业务中，评测和优化不能割裂：如果评测集质量差，优化器会过拟合；如果优化过程不可审计，改出来的 prompt 即使分数变高也很难进入生产。
该题要求构建一个“评测 - 失败归因 - prompt 优化 - 回归验证 - 产物审计”的自动闭环。它不是简单跑一次 AgentOptimizer，而是要判断优化是否真的提升、是否牺牲其他指标、是否出现过拟合、是否值得回写源 prompt。
任务描述
设计并实现一个可复现的 Evaluation + Optimization pipeline。输入 baseline prompt、训练评测集、验证评测集和优化配置，系统自动运行 baseline 评测、定位失败 case、执行若干轮优化、对候选 prompt 做验证集回归，并输出结构化优化报告和是否接受候选的决策。
具体要求
pipeline 至少需要包含以下阶段：
1.Baseline 评测：使用 AgentEvaluator 对训练集和验证集分别打分，记录每条 case 的 metric 分、pass/fail、失败原因和关键轨迹。
2.失败归因：按失败类型聚类，例如最终回复不匹配、工具调用错误、参数错误、LLM rubric 不达标、知识召回不足、格式不符合要求。
3.优化执行：使用 AgentOptimizer 或等价扩展机制优化至少一个 TargetPrompt，支持 system prompt、skill prompt、router prompt 中的一种或多种。
4.候选验证：优化后必须重新跑验证集，并和 baseline 做逐 case 对比，区分新增通过、新增失败、分数提升、分数下降。
5.接受策略：实现可配置 gate，例如验证集总分提升 ≥ 指定阈值、不能新增 hard fail、关键 case 不能退化、成本不能超过预算。
6.审计落盘：保存每轮候选 prompt、评测结果、接受/拒绝理由、运行成本、耗时、随机种子或复现实验配置。
输入输出要求：
●输入包含 train.evalset.json、val.evalset.json、optimizer.json 和 prompt 源文件。
●输出 optimization_report.json，包含 baseline、candidate、delta、gate decision、失败归因统计。
●输出 optimization_report.md，用人能读懂的方式说明优化是否值得接受。
●支持 fake judge / fake model / trace mode，保证没有真实 API Key 时也能跑通核心流程。
交付物
●新增示例目录，例如 examples/optimization/eval_optimize_loop/。
●pipeline 入口脚本、样例 evalset、样例 prompt、优化配置和 README。
●至少 6 条评测 case：3 条训练、3 条验证，其中需要包含可优化成功、优化无效、优化后退化三类情况。
●optimization_report.json 示例输出。
●一份 300 – 500 字方案设计说明，解释失败归因方法、接受策略、防过拟合策略和产物审计方式。
验收标准
1.公开提供的 6 条样例 case 必须全部可运行，并生成完整优化报告。
2.在隐藏样本上，优化接受/拒绝决策准确率 ≥ 80%。
3.对“验证集退化但训练集提升”的过拟合场景，必须能拒绝候选 prompt。
4.失败归因分类准确率 ≥ 75%，且每个失败 case 至少能给出一个可解释原因。
5.fake model / trace mode 下完整 pipeline 耗时 ≤ 3 分钟。
6.报告必须包含 baseline 分数、candidate 分数、逐 case delta、gate 决策、拒绝或接受理由。

