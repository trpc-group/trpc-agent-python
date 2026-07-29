# 方案设计

管线把当前源提示词定义为 Champion，把 `AgentOptimizer.optimize(update_source=False)` 产生的最佳提示词或显式候选定义为 Challenger。优化器只使用训练集发现问题；独立验证集只参加回归和门禁，避免把验证失败细节反馈给优化器。fake 模式由提示词内公开控制项生成可评测轨迹，仅验证流程，不作为隐藏样本能力证明。

每次运行以 UTC 微秒时间和随机后缀生成唯一目录，冻结提示词、数据集、评测配置、优化配置、Gate、模型和随机种子的哈希或标识。Champion 与 Challenger 均由 `AgentEvaluator` 评分；逐 case 保存实际/预期回复、指标原因、工具调用与响应、参数差异及 trace 引用。归因依据这些证据输出回复、格式、工具、参数、rubric、知识、基础设施或证据不足类别，不依赖场景标签。

Gate 同时检查验证提升、训练涨验证跌的过拟合、新增高风险失败、protected case 与 slice 退化、成本证据和微小波动。成本未知时保存空值并由 G6 拒绝自动应用。评测中的临时替换、异常恢复和最终写回都经 `TargetPrompt`；只有显式 `--apply` 且全部 Gate 通过才更新 Champion。JSON 与 Markdown 报告记录决策、每轮候选、耗时、成本、产物路径和复现命令，优化失败也会落盘可审计的 REJECT 报告。

你在可信的本地仓库 trpc-agent-python 中工作，目标是完成 GitHub Issue #91：

“构建一个可复现的 Evaluation + Optimization pipeline：
评测 → 失败归因 → Prompt 优化 → 回归验证 → 产物审计。”

请连续完成以下工作；只有在确实无法从仓库源码判断时才提问。完成后不要 git commit、不要 git push、不要修改或删除当前任务范围之外的文件。

# 总体边界

1. 只新增或修改：
   examples/optimization/eval_optimize_loop/
2. 不修改 trpc_agent_sdk/、tests/ 现有 SDK 测试、项目依赖、CI、git 配置。
3. 不做 Web UI、数据库、远端 Prompt Store、多 Agent 编排、MCP 服务。
4. 不复制其他 PR 的代码；可以复用当前仓库公开 API 和已有 example 的编码风格。
5. 不运行全仓 pytest；Windows 存在与本任务无关的 POSIX 平台失败。
6. 使用相对 evalset 路径，避免 Windows 盘符解析问题。
7. 所有写 Prompt 的操作必须通过 TargetPrompt 的公开 API，并正确 await read_all/write_all。
8. 默认绝不改 prompts/system.md。只有 gate=ACCEPT 且 CLI 显式传入 --apply 时才写回。
9. 不使用 monkeypatch SDK 私有成员，不新增 jsonschema 或第三方依赖。

# 要保留的设计主线

当前 Prompt 是 Champion；新 Prompt 是 Challenger。
候选不能因为训练集分高就自动成为 Champion，必须经过独立验证与门禁。
任何证据缺失都不能自动接受候选。

训练集 = 用于优化器发现问题；
验证集 = 独立裁判，不能把其详细失败内容喂回优化器。

# 必须交付的目录内容

在 examples/optimization/eval_optimize_loop/ 内实现：

- README.md：中文优先，80 行左右，说明目标、运行命令、三种场景、产物。
- DESIGN.md：简洁说明 Champion/Challenger、数据隔离、gate、fake/live 两种模式。
- pipeline.py：唯一 CLI 入口。
- runner.py：运行 Champion 与 Challenger 的评测、冻结输入、确保恢复 Prompt。
- attribution.py：把失败结果归类为：
  reply_mismatch / format_fail / tool_call_error / param_error /
  rubric_fail / knowledge_fail / none
- gates.py：纯规则、可独立单测。
- report.py：输出 optimization_report.json 和 optimization_report.md。
- data/train.evalset.json：3 条。
- data/val.evalset.json：3 条。
- prompts/system.md。
- 必要的 fake 实现与 tests/。
- runs/ 输出目录应 gitignore。

核心 Python 文件保持精简；不要为了形式拆成大量 package。

# 两阶段实现

## Milestone A：确定性 fake 模式，必须完整可跑

实现 --mode fake，完全不需要 API Key。

fake agent 的行为必须由当前 Prompt 中公开、可读的标记决定，例如：
[[FORMAT_JSON]]
[[USE_CORRECT_TOOL]]
[[MEMORIZE_TRAIN]]

禁止根据 prompt hash 偷偷切换预录答案。
也禁止伪造总分；必须返回可评测的回答或工具轨迹，再由评测/规则计算结果。

必须内置三个可复现情景：

1. success
   Candidate 修复格式或工具问题，train 和 val 都提升，最终 ACCEPT。

2. no_effect
   Candidate 没有产生有效改善，最终 REJECT。

3. overfit
   Candidate 只记住 train 样本，train 提升而 val 退化，
   或新增高风险失败，最终 REJECT；理由必须明确提到“过拟合”。

## Milestone B：原生 AgentOptimizer 接入

实现 --mode optimize：

- 构造 TargetPrompt；
- 调用：
  await AgentOptimizer.optimize(
      config_path=...,
      call_agent=...,
      target_prompt=...,
      train_dataset_path=...,
      validation_dataset_path=...,
      output_dir=...,
      update_source=False,
  )
- 从结果中获取 best_prompts 作为 Challenger；
- 使用与 fake 模式相同的 runner / gates / report；
- 即使本地没有真实模型 API Key，也要做到配置缺失时报错清楚；
- fake 模式必须始终能完整跑通。

# Gate 规则

Gate 必须同时看 train 与 val，输出 ACCEPT 或 REJECT 以及逐条理由。

必须包含：

G1. validation 最小有效提升，例如 min_val_lift=0.02。
G2. train 提升而 validation 下降，视为 overfit，必拒绝。
G3. 不新增 hard fail。
G4. protected case 不退化。
G5. 关键 slice 平均分不超过 tolerance 地下降。
G6. 成本证据完整：
    - trace/fake 模式可写 cost_status="measured", cost=0，
      因为确实没有模型调用；
    - 无法采集真实模型成本时写 cost_status="unavailable"，
      并拒绝自动 ACCEPT，不能把未知成本写成 0。
G7. 明显微小的分数变化不算有效提升。

不要写互相重复的规则。
每条规则都必须有独立单元测试。

# 数据与报告

只使用 train / val 两个 evalset，各 3 条，eval_id 不能重叠。

报告 optimization_report.json 和 optimization_report.md 必须含：

- frozen：prompt、dataset、optimizer config、运行模式等哈希/版本信息；
- champion 与 challenger 的来源和 sha256；
- train / val 的 baseline、candidate、delta；
- 每个 case 的状态、分数、delta、slice、risk_level、失败分类；
- decision、违反的 gate、自然语言理由；
- audit：duration_seconds、cost_status、cost、applied、artifact 路径、可复现命令；
- candidate_source：candidate_file 或 agent_optimizer。

Markdown 和 JSON 表达同一套核心信息。

# Prompt 安全写回

候选评测时可临时写入 Challenger，但必须：

- 先保存 Champion snapshot；
- 用 try/finally 恢复；
- 测试 dry-run 后 prompts/system.md 的 sha256 不变；
- --apply + ACCEPT 才真正写回；
- --apply + REJECT 必须报出原因且源文件不变；
- 备份和写回也使用 TargetPrompt 的机制，不要 Path.write_text 另起一套逻辑。

# 测试要求

新增 tests 至少覆盖：

1. train / val eval_id 不重叠；
2. 六类失败归因；
3. G1-G7；
4. success / no_effect / overfit 三个端到端场景；
5. dry-run 后 Champion 不变；
6. --apply 成功与拒绝；
7. 无 API Key 的 fake mode；
8. 报告字段完整；
9. 目标目录测试总时长小于 180 秒。

先写测试，再实现对应最小代码。

# 验证命令

只运行：

python -m pytest examples/optimization/eval_optimize_loop/tests -q
python examples/optimization/eval_optimize_loop/pipeline.py --mode fake --scenario success
python examples/optimization/eval_optimize_loop/pipeline.py --mode fake --scenario no_effect
python examples/optimization/eval_optimize_loop/pipeline.py --mode fake --scenario overfit
python -m compileall -q examples/optimization/eval_optimize_loop
git diff --check
git status -sb

# 最终回复格式

完成后按以下格式汇报：

1. 已修改/新增文件；
2. 每个阶段如何对应 Issue #91；
3. 三个 fake 场景的实际输出与 decision；
4. pytest 结果、耗时、其他验证结果；
5. 未验证的 live optimize 风险；
6. git diff 摘要；
7. 不要提交或推送。