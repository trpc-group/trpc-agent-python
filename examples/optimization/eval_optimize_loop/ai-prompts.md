# Issue #91 开发过程记录

## 第 1 轮：Pipeline 架构与失败归因引擎

我给出了整体架构设计：7 阶段流水线（配置加载 → 基线评测 → 失败归因 → 优化执行 → 候选验证 → Gate 决策 → 报告生成），以及模块划分。

关键设计决策：
- 使用 SDK 的 `AgentEvaluator.evaluate_eval_set()` 而非自己实现评测逻辑，好处是天然继承 trace mode、parallelism、callbacks
- 失败归因不能只看 pass/fail，要从 eval_result 的 reason 字段做关键词匹配，归到 8 个根因类别
- Gate 不只是分数阈值，需要 5 维度检查：提升幅度、关键 case 保护、新增失败、成本预算、过拟合检测
- Fake mode 基于 evalset JSON 的 `actual_conversation` 字段做确定性模拟，零 API 成本

生成了 config.py（PipelineConfig + JSON 加载）、baseline.py（fake/sdk 双路径）、attribution.py（9 类 FailureCategory + 关键词归因）、gate.py（多维度 GateDecision）、validate.py（逐 case delta 对比）、report.py（JSON + Markdown 双格式）。

测试方面，我指定了每个模块的核心场景：config 测缺失文件/非法 JSON/默认值覆盖；baseline 测 fake 模式数据加载和 SDK stub；attribution 测 10 种 failure reason 的归类准确率；gate 测 7 种决策场景（accept/reject/needs_review 边界）；validate 测过拟合检测。

## 第 2 轮：优化执行 + 审计追踪

我要求补充两个缺失模块：optimize.py（封装 AgentOptimizer）和 tracing.py（审计追踪）。

optimize.py 的设计要点：
- Fake 模式基于归因结果模拟 GEPA 迭代：每轮"修复"一个失败类别，产生确定的 score 提升
- Live 模式调用 `AgentOptimizer.optimize()` + `TargetPrompt.add_path()`
- 记录每轮的 RoundRecord（score、prompt_changes、cost、duration）
- 异常处理：SDK 不可用时返回友好提示，引导安装 `trpc-agent-python[gepa]`

tracing.py 的设计要点：
- AuditTracer 类封装所有追踪逻辑：start_stage/end_stage 记录每个阶段的 wall clock
- add_cost 累计优化和评测成本
- record_input_file 计算 SHA-256 哈希确保可复现
- finalize 生成完整的 reproduce_command
- to_dict 输出 JSON-serializable dict，直接嵌入 optimization_report.json

同时要求创建 agent/ 包，包含一个简单的 calculator agent 用于测试。Fake 模式下用 question hash 做确定性输出，方便 trace mode 评测。

## 第 3 轮：大规模测试扩容

我审查了第一版测试后发现覆盖不够——35 个测试全挤在一个文件里，缺少集成测试、边界测试、性能测试和大规模数据测试。

按 6 个维度拆分测试：
1. 单元测试：每个 pipeline 模块一个独立文件，共享 conftest.py fixtures
2. 集成测试：完整 pipeline fake mode 端到端，包含多轮优化、过拟合拒绝、CI 模式
3. 大规模数据：50-100 case evalset 加载和归因，20 轮对话，多语言混合
4. 边界测试：空 evalset、单 case、500 字符 case_id、负 timeout、无效 JSON、emoji/Unicode
5. 回归测试：seed 确定性、优化无效时不修改源文件、gate REJECT 时报告仍生成
6. 性能测试：fake mode < 3s、50 case 加载 < 5s、100 case 端到端 < 10s

同时要求扩充 evalset 数据——从 6 个简单数学 case 扩展到 62 个跨领域 case（数学、多步推理、工具调用、中文、日文、韩文、emoji、格式要求），包含 holdout 隐藏集。

## 第 4 轮：修复与验证

审查发现几个问题：
- Windows GBK 编码导致 emoji 打印崩溃 → 替换为 ASCII 安全字符
- `_categorize_failure` 中 "missing" 关键词顺序在 "response" 之后，导致 "missing expected output" 被错误归类 → 调整判断顺序
- 测试中硬编码 `== 3` 的断言在 evalset 扩容后失败 → 改为 `>= 3`
- `test_large_scale` 中的 attribution 测试直接构造 BaselineResult 而非依赖 evalset 文件（因为 fake baseline 的 pass/fail 逻辑基于 conversation 字段存在性，不是内容匹配）
- test_performance.py 的 100 case 缩放比阈值太严，fast ops 下浮点精度导致误判 → 增加绝对上限

全部修复后，189 tests passed，pipeline fake mode 端到端验证通过。Commit，push 到 fork，PR 自动更新。
