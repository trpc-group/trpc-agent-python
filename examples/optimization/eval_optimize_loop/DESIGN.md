# Design Note — Eval-Optimize Loop

闭环以 `run_pipeline.py` 为唯一入口，将基线评测、失败归因、提示词优化、候选复评、接受门禁和审计落盘隐藏在同一深模块内。双模式设计是核心差异化：**fake 模式**无需外部依赖，秒级验证 6 阶段概念完整性，适合 CI 冒烟和快速迭代；**real 模式**对接真实 `AgentOptimizer.optimize()` + `AgentEvaluator.evaluate_eval_set()`，通过 `ModelRegistry` 注入离线模型使 SDK 代码路径全覆盖而无须外部 API key。

与通用数学 evalset 不同，本 pipeline 设计上对接 PlateAgent——含 30 张真实车牌图像、Tesseract 双通道 OCR 和 LLM Judge 评分体系的生产级 agent。`BaselineRunner._run_real_split()` 直接调用 `PlateEvaluator`，使优化闭环可在有可度量失败模式（模糊、噪声、字符混淆）的真实场景上验证，而非仅依赖合成 case。

归因模块按 6 个根因类别（答案不匹配/工具错误/参数错误/rubric 失败/知识召回不足/格式错误）输出证据链，优先级由配置驱动。Gate 对验证增益、新增 hard fail、关键 case 回退、成本预算和过拟合做可配置 AND 决策；fake 模式保留 `all_must_pass` 和 `majority` 两种策略，real 模式追加配对 bootstrap 下界。

审计层为每次运行保存完整 prompt before/after、change_log、种子和成本，双格式输出（JSON 程序消费 + Markdown 人类阅读），支持事后回溯任意候选的优化决策。
