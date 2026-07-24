# Issue #91 安全评测优化闭环设计

## 背景

PR #119 已经提供 fake 评测、候选 prompt、gate 和 JSON/Markdown 报告，但已发布提交中没有一条路径同时完成真实 baseline 评测、候选逐 case 复评、完整 gate 和 gate 后回写。fake model 还会读取 expectation、split、protected 等评测真值，因此现有测试不能证明隐藏样本决策准确率。SDK 模式把 `--update-source` 直接传给 `AgentOptimizer`，候选可能在 wrapper gate 拒绝前写入源 prompt。

本设计的边界是通过 Issue #91 的验收并安全合并。与验收无关的通用框架重构不纳入本次修改。

## 目标

1. fake 和 SDK 共用同一条“评测—归因—优化—复评—gate—审计—可选回写”流程。
2. SDK 使用真实 `AgentEvaluator`、`AgentOptimizer` 和 `TargetPrompt`，但外部模型调用仍可在 CI 中替换为确定性实现。
3. gate 只消费完整、可核验的逐 case 结果；不再允许 `cases=[]` 或 `partial_applied` 的候选被选中。
4. 源 prompt 只在候选通过完整 gate 且用户显式指定 `--update-source` 后写回。
5. 提供可执行证据证明隐藏决策准确率、失败归因准确率、过拟合拒绝和三分钟性能要求。
6. 报告、输入哈希、耗时、成本和运行产物可以复核，不泄露贡献者机器的绝对路径。

## 非目标

- 不修改 `AgentEvaluator`、`AgentOptimizer` 或 GEPA 的公共 API。
- 不引入新的远程服务、数据库或长期任务系统。
- 不要求 CI 使用真实 API key。
- 不把示例扩展成通用生产部署平台。

## 选定方案

采用统一闭环编排器。fake 与 SDK 仅负责各自的 evaluate/optimize 适配，候选选择、gate、审计和写回语义由共享 pipeline 实现。

分别修补两条路径虽然改动较少，但会保留两套不同的 gate 和报告语义；完全 SDK-first 会放大重构范围。统一编排器在验收覆盖、维护成本和合并风险之间更平衡，并可直接承接当前工作树中已有的 SDK EvalSet 与 AgentEvaluator 适配工作。

## 组件与文件职责

### `run_pipeline.py`

保留 CLI、路径解析和同步兼容入口。新增异步核心入口的薄包装：同步 `run_pipeline()` 在没有活动事件循环时调用 `asyncio.run(run_pipeline_async(...))`；异步调用者直接使用 `run_pipeline_async()`。该文件不再实现候选循环、SDK 报告拼装或写回逻辑。

### `eval_loop/pipeline.py`

新增共享编排器，负责：

1. 冻结输入与 prompt 快照；
2. baseline train/validation 评测；
3. 失败归因；
4. 调用 optimizer backend；
5. 对每个候选重新执行 train/validation 评测；
6. 计算逐 case delta 和完整 gate；
7. 选择候选；
8. 写审计产物；
9. 在允许时执行安全回写。

### `eval_loop/backends.py`

backend 统一暴露异步接口：

```python
class EvaluationBackend(Protocol):
    async def evaluate(
        self,
        *,
        prompt_id: str,
        prompts: dict[str, str],
        dataset_path: Path,
        split: str,
        trace: bool,
        artifact_dir: Path,
    ) -> EvalResult: ...


class OptimizationBackend(Protocol):
    async def optimize(
        self,
        *,
        baseline_prompts: dict[str, str],
        baseline_train: EvalResult,
        failure_summary: dict[str, object],
        train_path: Path,
        validation_path: Path,
        config_path: Path,
        artifact_dir: Path,
    ) -> OptimizationResult: ...
```

`SDKBackend.optimize()` 始终调用 `AgentOptimizer.optimize(update_source=False)`。它从 `OptimizeResult.rounds[].candidate_prompts` 和 `best_prompts` 提取、去重候选；每个候选由共享 pipeline 做完整 train/validation 复评。`SDKBackend.evaluate()` 使用真实 `AgentEvaluator`，并把每个 case 的逐 metric 分数、pass/fail、原因、证据和可用 trace 转为统一 schema。关键轨迹从 `EvalCaseResult.eval_metric_result_per_invocation[].actual_invocation` 提取用户输入、工具调用和最终回复；SDK 没有提供某类轨迹时显式设置 `trace_available=False`，不能伪造空轨迹为“已采集”。

`FakeBackend.optimize()` 只接收 baseline train 结果、失败归因和 optimizer 配置，不接收 validation 评测结果。它根据观察到的失败类别生成确定性候选，而不是无条件返回固定候选。

### `eval_loop/gate.py`

保留唯一 gate 实现。gate 检查：

- validation 总分提升阈值；
- 训练提升但 validation 不提升的过拟合；
- 新 hard fail；
- protected case 退化；
- 单 case 最大降分；
- 可选成本预算；
- baseline 与 candidate case ID 集合一致。

候选缺失逐 case 数据、成本 gate 所需数据不完整或评测失败时，gate 必须拒绝并返回明确原因。

### `eval_loop/writeback.py`

新增 prompt 快照和回写组件：

- 保存每个 prompt 文件的原始字节与 SHA-256；
- 候选复评期间临时应用 prompt，并在 `finally` 中恢复；
- 恢复后校验哈希，恢复失败立即终止 pipeline；
- 最终回写前执行 compare-and-swap 检查，避免覆盖并发修改；
- 多 prompt 使用临时文件和 `os.replace`，任一失败时回滚所有已写字段；
- 返回结构化 `WritebackResult`。

### `eval_loop/report.py`

只负责 schema 序列化、Markdown 渲染和 run-specific 审计目录。它不再推导 gate 或 SDK 特殊结果。

## 统一数据模型

`CaseResult` 增加逐 metric 数据和 trace 可用性：

```python
@dataclass(frozen=True)
class CaseResult:
    case_id: str
    split: str
    score: float
    metrics: dict[str, float]
    passed: bool
    output: str
    trace: dict[str, object]
    trace_available: bool
    failure_category: str | None
    failure_reason: str | None
    evidence: str | None
    cost: float
    hard_failed: bool
    expected_failure_category: str | None
```

新增以下审计模型：

- `OptimizationRound`：round ID、候选 prompt bundle、修改理由、optimizer 指标、成本和耗时；
- `CostSummary`：optimizer、evaluator、agent、total 和 `complete`；
- `WritebackResult`：`rejected` 表示没有候选通过 gate，`not_requested` 表示候选通过但用户未要求回写，`applied` 表示回写成功，`rolled_back` 表示写入失败且已完整恢复，`rollback_failed` 表示恢复不完整；同时记录前后哈希和错误原因；
- `OptimizationResult`：所有去重候选、round 记录和 backend 原始摘要。

## 端到端数据流

1. 解析并严格验证四类输入文件、gate 配置和 target prompt 路径。
2. 生成唯一 `run_id`，创建 `runs/<run_id>.tmp/`，启动 `perf_counter`。
3. 对输入文件和源 prompt 做字节级快照与 SHA-256。
4. 用 baseline prompt 分别评测 train 和 validation；任一 baseline 评测缺 case 或恢复 prompt 失败时终止。
5. 仅根据 baseline train 失败结果生成失败归因和候选。
6. 对每个候选分别完整评测 train 和 validation，校验 case ID 一致，再计算 delta。
7. 对每个候选执行同一完整 gate，并从已接受候选中按 validation、train、原始顺序稳定选择最佳项。
8. 写候选、round、评测结果、delta、gate、输入快照哈希和真实耗时到临时 run 目录。
9. 若没有接受候选或未指定 `--update-source`，记录对应 writeback 状态。
10. 若允许回写，先确认源文件仍与起始快照一致，再执行原子多文件写入；失败则回滚并记录原因。
11. 完成 `writeback.json`、最终 JSON/Markdown 后，把临时目录原子重命名为 `runs/<run_id>/`。已存在的 run ID 不允许覆盖。
12. 根目录 `optimization_report.json` 和 `.md` 仅作为最新结果的便利副本；不可变证据以 run 目录为准。

## Fake 模式隔离规则

fake model 只能读取用户输入和当前 prompt 文本。evalset 的 expectation、expected answer、split、protected 和标签只能由 evaluator、归因器或 gate 使用。

公开样例输入改成包含明确业务值的指令，例如 `intent=refund, priority=high`。fake model 从用户指令解析值：baseline 对严格格式请求加入多余说明，过度候选对所有请求强制 JSON，安全候选只在用户明确要求时使用严格格式。相同输入和 prompt 在 train/validation 中必须产生相同输出。

fake optimizer 根据 baseline train 中的失败类别选择规则模板。例如发现 format 与 exact-answer 失败时，生成一个全局严格候选和一个按请求限定的候选。它不得读取 validation 结果或 case ID。

## 成本语义

`max_total_cost` 是可选 gate。`CostSummary.complete=True` 时，total 必须是已知 optimizer、evaluator 和 agent 成本之和。fake backend 提供完整成本。

SDK 无法获得 agent/provider 完整成本时设置 `complete=False`。若配置了成本上限，候选必须以 `cost_unavailable` 拒绝；未配置成本上限时可以继续其他 gate，但报告必须把 SDK 提供的数值标记为 `reported_optimizer_cost`，不能称为完整总成本。

## 错误处理

- baseline 输入、评测或 prompt 恢复失败：终止运行，不选择候选，不最终回写。
- 单个候选评测失败：记录候选拒绝原因，继续评测其他候选。
- case ID 缺失、重复或集合不一致：该候选拒绝。
- 非有限数值、非标准 JSON、重复 target path、split 冲突：输入阶段直接报错。
- 最终回写 compare-and-swap 失败：不覆盖源文件，记录并抛出并发修改错误。
- 多文件写入失败：回滚已写字段；回滚成功时状态为 `rolled_back`，回滚失败时状态为 `rollback_failed` 并报告受影响路径。
- 审计写入失败：最终回写不会开始。

## 审计产物

每个 `runs/<run_id>/` 至少包含：

- `optimization_report.json` 与 `optimization_report.md`；
- `input_hashes.json` 和规范化配置快照；
- `baseline_prompts/`、`candidate_prompts/` 与 `prompt_diffs/`；
- `case_results/` 和 `per_case_deltas.json`；
- `rounds/`，保存每轮候选、理由、成本、耗时和 optimizer 指标；
- `gate_decisions.json`；
- `writeback.json`；
- SDK 模式下的 `sdk_optimizer/` 原始产物。

报告路径优先保存仓库相对路径；仓库外输入仅保存用户传入的规范化路径，不写入维护者机器生成样例。示例报告由测试临时生成并与 committed inputs 的哈希自动比对。

## 测试设计

### Backend 契约测试

fake 与 SDK backend 都必须返回完整、case ID 唯一且集合一致的 `EvalResult`。SDK 测试使用真实 `AgentEvaluator`、`AgentOptimizer` 和 `TargetPrompt`；仅 monkeypatch GEPA/外部反思模型调用，沿用仓库现有 facade 测试模式。

### 安全回写测试

- gate 拒绝且传入 `--update-source` 时，所有源 prompt 字节和哈希保持不变；
- gate 接受时才写入选中候选；
- 多 prompt 第二个字段写入失败时，第一个字段回滚；
- 起始快照后发生并发修改时拒绝覆盖；
- 审计写入失败时不触发回写。

### 隐藏决策准确率

新增至少 10 个与公开六例分离的 holdout 场景，覆盖安全提升、无提升、训练提升而 validation 退化、protected regression、new hard fail、单 case drop 和超预算。标签与 pipeline 输入分离，计算 `correct / total` 并断言 `>= 0.80`。

### 失败归因准确率

新增独立归因语料，覆盖 format、final response、tool、parameter、rubric 和 knowledge 类别。归因器只接收评测错误和证据，不接收标签；断言准确率 `>= 0.75`，且每个失败都有非空 reason 与 evidence。

### 性能与报告

用 wall clock 运行完整 fake+trace pipeline，断言少于 180 秒；报告中的 duration 必须大于零且不大于测试观测值的合理上界。测试重新生成 example report，校验输入哈希、相对路径、严格 JSON、全部必需字段和旧 run 不被覆盖。

## 兼容性与迁移

- 保留现有 CLI 参数和同步 `run_pipeline()`；新增异步入口不破坏已有调用方。
- fake 和 SDK 输入统一使用官方 SDK EvalSet 形状；loader 在本次 PR 内继续兼容旧 `cases` 形状，但 README 和 committed examples 只展示官方形状。
- JSON schema 版本提升，新增字段不复用旧字段表达不同语义。
- `--update-source` 的用户语义保持不变，但实际执行从 optimizer 内部提前写入改为完整 gate 后写入。

## 合并标准

以下条件全部满足后才建议合并：

1. 六个公开 case 完整运行并生成 JSON、Markdown 和审计目录；
2. SDK 路径提供真实 baseline/candidate 逐 case 结果，不存在可被接受的 partial gate；
3. 训练提升而 validation 退化的候选被拒绝；
4. holdout 决策准确率至少 80%；
5. 独立归因准确率至少 75%，所有失败可解释；
6. fake+trace wall clock 少于三分钟且报告记录真实耗时；
7. gate 拒绝、评测失败、审计失败和并发修改时源 prompt 不被覆盖；
8. committed example report 的哈希与 committed inputs 一致且没有贡献者绝对路径；
9. 目标测试、完整示例测试、仓库 lint、build 和 CI 全部通过。
