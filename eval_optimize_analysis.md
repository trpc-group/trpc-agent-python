# Evaluation + Optimization 分析报告

> 本报告基于 `trpc_agent_sdk/evaluation/`、`tests/evaluation/`、`examples/optimization/` 的源码与 `pytest_full.log` 的实测数据，回答四个问题：
> 1. AgentEvaluator 与 AgentOptimizer 的相关文件路径及职责
> 2. 一条从 evalset 输入到评测结果、再到优化器输出的真实调用链
> 3. evaluation 测试失败的完整 traceback
> 4. #91 新增示例目录应放在哪里
>
> 本报告**不修改代码、不修复 Windows 平台问题**，仅做架构梳理与决策建议。

---

## 0. 任务规格回顾（来自 `Evaluation + Optimization.md`）

构建"评测 → 失败归因 → prompt 优化 → 回归验证 → 产物审计"的自动闭环。输入 `train.evalset.json` / `val.evalset.json` / `optimizer.json` / 源 prompt，输出 `optimization_report.json` / `.md`，要求：

- 6 条 case：3 训练 + 3 验证，覆盖**可优化成功 / 优化无效 / 优化后退化**三类场景
- fake judge / fake model / trace mode 下 ≤3 分钟跑通
- 隐藏样本上接受/拒绝决策准确率 ≥80%
- 对"验证集退化但训练集提升"的过拟合场景必须拒绝
- 失败归因分类准确率 ≥75%
- 报告必须包含 baseline 分数、candidate 分数、逐 case delta、gate 决策、拒绝/接受理由

---

## 1. 相关文件路径及职责

### 1.1 核心模块（`trpc_agent_sdk/evaluation/`）

| 文件 | 职责 |
|---|---|
| `__init__.py` | 对外总入口，导出 `AgentEvaluator` / `AgentOptimizer` / `TargetPrompt` / `EvalSet` / `EvalCase` / `EvaluateResult` / `OptimizeResult` 等 ~150 个符号 |
| `_agent_evaluator.py` | **评测入口**。`AgentEvaluator.evaluate()` / `get_executer()` 静态方法；`_EvalExecuter._run()`（L140-231）发现 `.evalset.json` → 逐文件加载 → 调 `evaluate_eval_set()` → 汇总打印 → 若有失败 `raise _EvaluationCasesFailed`（L78-93，`AssertionError` 子类，兼容 CI `except AssertionError`）|
| `_agent_optimizer.py` | **优化器门面**。`AgentOptimizer.optimize()` 类方法（L132-296）：预检算法名 → 加载 `OptimizeConfigFile` → 校验输入（`_validate_inputs` L544-614）→ 从 `OPTIMIZER_REGISTRY` 实例化算法 → 调 `optimizer.run(reporter=...)` → 写 `result.json` / `summary.txt` / `rounds/` / `best_prompts/` / `baseline_prompts/` / `config.snapshot.json` / `run.log`。`_mask_sigint`（L72-109）保证 Ctrl+C 期间产物落盘 |
| `_optimize_evaluator_call.py` | **桥接层**。`run_evaluator()`（L83-136）= 优化器调评测器的标准入口：`AgentEvaluator.get_executer()` → `await executer.evaluate()` → 捕获 `_EvaluationCasesFailed`（业务信号，吞掉）→ 其它异常透传 → `summarize_outcome(result)` 输出 `EvaluationOutcome`（`pass_rate` / `tiebreaker` / `metric_breakdown` / `failed_case_ids`）|
| `_base_optimizer.py` | 抽象基类 `BaseOptimizer`。定义 `__init__` 接收 config / call_agent / target_prompt / train / val 路径；声明抽象 `run(reporter=)`；提供 `resolve_required_thresholds`（L80-106）与 `metrics_meet_thresholds`（L108-123）给 framework-level stop policy 复用 |
| `_optimize_gepa_reflective.py` | **GEPA 反思算法实现**（当前 `OPTIMIZER_REGISTRY` 唯一注册项）。`GepaReflectiveOptimizer.run()`（L431-）→ 读 baseline prompts → 加载 train/val evalset → 构造 `_AgentGEPAAdapter` + `_OptimizeModelCallable` → 在 worker thread 调 `gepa.optimize(...)`（L425-429，`asyncio.to_thread`）；`_build_stop_callbacks`（L294-381）把 `max_metric_calls` / `no_improvement` / `timeout` / `score_threshold` / `max_candidate_proposals` / `max_tracked_candidates` / `optimize.stop` 文件 / framework `required_metrics` 都翻译成 gepa `StopperProtocol` |
| `_optimize_gepa_adapter.py` | **GEPA 协议适配器**。`_AgentGEPAAdapter` 实现 `gepa.core.adapter.GEPAAdapter`：`evaluate(candidate, subsample)` 调 `run_evaluator`；`make_reflective_dataset()`（L13-）把失败 case 渲染成 turn-sliced markdown 喂给 reflection LM（含 PASS/FAIL 行、rubric 子项分、synthesized failure reason） |
| `_optimize_gepa_callback.py` | `_AgentGEPACallback`：gepa 事件 → `RoundRecord` 实时缓冲，是 `_build_optimize_result` 的 round 数据首选来源 |
| `_optimize_model_callable.py` | `_OptimizeModelCallable`：把 `OptimizeModelOptions` 包装成 gepa 可调用的 reflection LM |
| `_optimize_config.py` | 配置 schema：`OptimizeConfigFile`（顶层）/ `EvalConfig`（评测）/ `FrameworkStopConfig`（`stop.required_metrics`）/ `GepaReflectiveAlgo`（算法超参，含 `max_metric_calls` / `max_iterations_without_improvement` / `reflection_minibatch_size` 等）|
| `_optimize_registry.py` | `OPTIMIZER_REGISTRY = OptimizerRegistry()`；`_optimize_registrations.py` 触发 gepa_reflective 注册 |
| `_optimize_reporter.py` | `OptimizeReporter` Rich 面板 + ASCII fallback；`RoundView` / `RunHeader` 数据类 |
| `_optimize_result.py` | 结果 schema：`OptimizeResult`（顶层）+ `RoundRecord`（每轮）+ `StopReason` / `RunStatus` / `FinishReason` Literal 类型 |
| `_target_prompt.py` | **多字段 prompt 注册表**。`TargetPrompt.add_path(name, path)` / `.add_callback(name, read=, write=)`；`read_all()` / `write_all()` 原子（path-backed 用 tmp+`os.replace`，部分失败时回滚）|
| `_eval_set.py` | `EvalSet` Pydantic 模型：`eval_set_id` / `app_name?` / `name?` / `description?` / `eval_cases: list[EvalCase]` |
| `_eval_case.py` | `EvalCase` / `Invocation` / `ConversationScenario` / `StaticConversation`；`EvalModeTrace = "trace"`（不跑 agent，直接评测预录对话）|
| `_eval_config.py` | `EvalConfig`：`criteria` (旧式) 或 `metrics` (新式) + `num_runs` |
| `_eval_metrics.py` | `EvalMetric` / `EvalStatus(PASSED\|FAILED)` / `PrebuiltMetrics` 枚举（`final_response_avg_score` 等）|
| `_eval_result.py` | `EvalCaseResult` / `EvalSetAggregateResult` / `EvaluateResult` / `EvalMetricResult`（含 score/threshold/eval_status/details）|
| `_eval_service_base.py` | `BaseEvalService` / `InferenceRequest` / `EvaluateRequest` / `InferenceConfig` / `EvaluateConfig` |
| `_local_eval_service.py` | `LocalEvalService`：本地驱动 agent 推理 + 评测 |
| `_remote_eval_service.py` | `RemoteEvalService` + `CallAgent = Callable[[str], Awaitable[str]]`：黑盒模式，业务回调驱动 agent |
| `_final_response_evaluator.py` | `FinalResponseEvaluator`：精确 / 包含 / 正则 / JSON 匹配 |
| `_trajectory_evaluator.py` | `TrajectoryEvaluator`：工具调用轨迹评分（需 session traces）|
| `_rouge_evaluator.py` | `RougeEvaluator`：ROUGE 分 |
| `_llm_criterion.py` / `_llm_evaluator.py` / `_llm_judge.py` | LLM-as-judge 三件套：rubric 打分 / 知识召回 / 最终回复质量，支持 multi-model judges / weighted aggregators |
| `_eval_callbacks.py` | `Callbacks`（生命周期钩子）+ `CallbacksRunner` |
| `_local_eval_sets_manager.py` / `_in_memory_eval_sets_manager.py` / `_local_eval_set_results_manager.py` | evalset 持久化与历史结果管理 |

### 1.2 测试目录（`tests/evaluation/`，53 个文件）

按职责分组：

- **AgentEvaluator 主流程**：`test_agent_evaluator.py`、`test_agent_evaluator_call_agent.py`
- **AgentOptimizer 门面**：`test_agent_optimizer.py`
- **桥接层**：`test_optimize_evaluator_call.py`
- **GEPA 四件套**：`test_optimize_gepa_adapter.py`、`test_optimize_gepa_callback.py`、`test_optimize_gepa_e2e.py`、`test_optimize_gepa_reflective.py`
- **端到端**：`test_optimize_quickstart_example.py`（以 `examples/optimization/quickstart/` 为素材）
- **组件级**：`test_optimize_config.py`、`test_optimize_result.py`、`test_optimize_reporter.py`、`test_optimize_registry.py`、`test_optimize_model_callable.py`、`test_optimize_model_options.py`、`test_optimize_metric_info.py`
- **TargetPrompt**：`test_target_prompt.py`
- **数据模型**：`test_eval_case.py`、`test_eval_set.py`、`test_eval_config.py`、`test_eval_metrics.py`、`test_eval_pass.py`、`test_eval_result.py`
- **回调**：`test_eval_callbacks.py`、`test_eval_callbacks_ext.py`
- **基础设施**：`test_eval_session_service.py`、`test_eval_service_base.py`、`test_local_eval_sets_manager.py`、`test_in_memory_eval_sets_manager.py`、`test_remote_eval_service.py`
- **Evaluator 实现**：`test_final_response_evaluator.py`、`test_trajectory_evaluator.py`、`test_rouge_evaluator.py`、`test_llm_judge*.py`、`test_llm_criterion.py`、`test_llm_evaluator_registry.py`

### 1.3 现有 examples 目录（`examples/optimization/`）

```
examples/optimization/
├── quickstart/                 # 入门示例（小学算术题，2 个 prompt 文件）
├── advanced_strategies/        # 进阶策略（pareto / current_best / merge）
├── blackbox_cli/               # CLI 黑盒 agent 接入
├── ci_integration/             # pytest + AgentOptimizer 闭环
├── http_service/               # HTTP 服务形态
├── multi_agent_pipeline/       # 多 agent 编排
├── multi_metric_with_judges/   # 多 metric + 独立 judge model
├── remote_prompt_store/        # 远端 prompt 源（Redis / HTTP）
└── slo_runtime_control/        # SLO / budget / kill switch
```

每个子目录都有 `README.md` + `optimizer.json` + 可独立运行的入口脚本 + `train.evalset.json` + `val.evalset.json`。

---

## 2. 真实调用链：evalset → 评测结果 → 优化器输出

以 `examples/optimization/quickstart/run_optimization.py` 为例，从 `asyncio.run(main())` 到产物落盘的完整链路：

### 阶段 A：业务组装（`run_optimization.py`）

```python
TargetPrompt().add_path("system_prompt", SYSTEM_PROMPT_PATH)
              .add_path("skill",        SKILL_PATH)
await AgentOptimizer.optimize(
    config_path="optimizer.json",
    call_agent=call_agent,                  # 业务回调
    target_prompt=target,
    train_dataset_path="train.evalset.json",
    validation_dataset_path="val.evalset.json",
    output_dir="runs/<timestamp>",
    update_source=False,
)
```

### 阶段 B：`AgentOptimizer.optimize` 门面（`_agent_optimizer.py:132-296`）

1. `_precheck_algorithm_name(config_path)` L200 → 读 raw JSON，查 `OPTIMIZER_REGISTRY`，未注册则 fail-fast
2. `load_optimize_config(config_path)` L201 → pydantic 校验，输出 `OptimizeConfigFile`
3. `_validate_inputs(...)` L202-209 → async check / train≠val / disallowed metrics in call_agent mode / use_merge≥2 fields
4. `os.makedirs(output_dir)` L210
5. `OPTIMIZER_REGISTRY.get("gepa_reflective")` L213 → `GepaReflectiveOptimizer`
6. 实例化 optimizer L214-224，注入 config / call_agent / target_prompt / train / val / output_dir
7. `create_reporter(verbose=1, stream=sys.stdout)` L226
8. `baseline_snapshot = await target_prompt.read_all()` L227（**关键：快照基线 prompt，供 finally 回滚**）
9. `_build_run_header(...)` L228-235 → 读 train/val evalset 数 case 个数
10. `reporter.run_started(header)` L236
11. `try: result = await optimizer.run(reporter=reporter)` L251
12. `if update_source and result.status == "SUCCEEDED": await target_prompt.write_all(result.best_prompts)` L256-264
13. `finally:` L272 — 若 cleanup 未完成，回滚到 `baseline_snapshot`；调 `_persist_artifacts` 写产物；`reporter.run_finished/failed`

### 阶段 C：`GepaReflectiveOptimizer.run`（`_optimize_gepa_reflective.py:431-`）

1. `baseline_prompts = await self.target_prompt.read_all()` L441
2. `_load_evalset_cases(train_path)` + `_load_evalset_cases(val_path)` L445-446 — `Path.read_text` + `EvalSet.model_validate_json` → `list[EvalCase]`
3. 构造 `_AgentGEPAAdapter(target_prompt, eval_config, call_agent, callbacks, num_runs, case_parallelism, reflection_history_top_k)` L457-465
4. 构造 `_OptimizeModelCallable(algo.reflection_lm)` L466 — 包装 reflection LM
5. 调 `_run_with_adapter(...)` L469-481，`finally: adapter.close()`

### 阶段 D：`_run_with_adapter` → `gepa.optimize(...)`

- `_build_stop_callbacks(algo, stop_config, metric_thresholds, output_dir)` L294-381 把配置翻译成 gepa stopper 列表
- `await self._call_gepa_optimize(**kwargs)` L425-429 → `asyncio.to_thread(gepa_optimize, **kwargs)` 把同步 GEPA 主循环扔到 worker thread，不阻塞 surrounding event loop
- GEPA 主循环每轮：
  1. `module_selector` 选要改写的 prompt 字段（`round_robin` 在 system_prompt ↔ skill 间交替）
  2. 从 trainset 抽 `reflection_minibatch_size` 条 case
  3. `adapter.evaluate(parent, subsample)` → **下到阶段 E**
  4. `adapter.make_reflective_dataset(failed_cases)` → 渲染 markdown 反思素材
  5. reflection LM 生成新候选 prompt → `target_prompt.write_all(candidate)` 写回磁盘
  6. `adapter.evaluate(candidate, full_valset)` → **下到阶段 E**
  7. Pareto 前沿比较，决定接受 / 拒绝
  8. `_AgentGEPACallback` 实时缓冲 `RoundRecord`
  9. stop callbacks 投票，命中则退出

### 阶段 E：`_AgentGEPAAdapter.evaluate` → `run_evaluator`（`_optimize_evaluator_call.py:83-136`）

```python
executer = AgentEvaluator.get_executer(
    eval_dataset_path,                        # 临时文件 or val.evalset.json
    call_agent=call_agent,                    # 业务回调
    callbacks=callbacks,
    num_runs=num_runs,
    print_detailed_results=False,             # 优化器静默评测
    print_summary_report=False,
    eval_metrics_file_path_or_dir=eval_metrics_path,
    case_parallelism=case_parallelism,
)
try:
    await executer.evaluate()
except _EvaluationCasesFailed:
    pass                                      # 业务信号，吞掉
result = executer.get_result()
return summarize_outcome(result)              # → EvaluationOutcome
```

### 阶段 F：`_EvalExecuter._run`（`_agent_evaluator.py:140-231`）

1. `_resolve_shared_config(eval_metrics_file_path_or_dir)` L156
2. 遍历 `.evalset.json` 文件 L158-165
3. 对每个文件：`_load_eval_set_from_file` L187（支持 `file.json:case_id` 选择子集）→ `EvalSet.model_validate_json`
4. `evaluate_eval_set(eval_set, call_agent=call_agent, eval_config=eval_config, ...)` L188-202
5. 收集 failures，构造 `EvalSetAggregateResult`
6. `_RESULT_HANDLER.print_evaluation_report` 打印
7. 若 `all_failures`：`raise _EvaluationCasesFailed(json.dumps(...))` L231 — **此时 `self._result` 已被赋值**，优化器能拿到

### 阶段 G：`AgentEvaluator.evaluate_eval_set`（`_agent_evaluator.py:470-586`）

1. 校验：`call_agent` / `agent_module` / `runner` 互斥；trace_only 模式可省 agent
2. `_get_eval_results_by_eval_id(...)` L540-552 — **下到阶段 H**
3. 后处理：`_get_eval_metric_results_with_invocation` L561 翻转 grouping
4. `_RESULT_HANDLER.process_metrics_and_get_failures` L563-570 提取失败 case
5. `build_summary` + `build_evaluation_result_lines` 输出表格
6. 返回 `(failed_summary, details_lines, result_lines, eval_results_by_eval_id)`

### 阶段 H：`_get_eval_results_by_eval_id`（`_agent_evaluator.py:815-915`）

1. `InMemoryEvalSetsManager` 创建并填充 evalset
2. 选择 `RemoteEvalService`（call_agent 模式）或 `LocalEvalService`
3. `InferenceConfig(parallelism=case_parallelism)` 构造 N 份 `InferenceRequest`（N=num_runs）
4. `async for inference_result in eval_service.perform_inference(...)` L893 — 调 `call_agent(query)` 拿 agent 回复
5. `EvaluateConfig(eval_metrics=eval_metrics)` + `EvaluateRequest(inference_results=inference_results)` L901-906
6. `async for eval_result in eval_service.evaluate(evaluate_request)` L908 — 跑每个 metric 的 evaluator（`FinalResponseEvaluator` / `LLMRubricResponseEvaluator` 等）
7. 按 eval_id 聚合，返回 `dict[str, list[EvalCaseResult]]`

### 阶段 I：结果回传与产物落盘

- 阶段 H 结果 → 阶段 G `eval_results_by_eval_id` → 阶段 F `EvaluateResult(results_by_eval_set_id)` → 阶段 E `summarize_outcome(result)` 输出 `EvaluationOutcome(pass_rate, tiebreaker, metric_breakdown, failed_case_ids)`
- GEPA 用 `EvaluationOutcome.pass_rate` 比较 Pareto 前沿
- 主循环结束后，阶段 C `_build_optimize_result`（`_optimize_gepa_reflective.py:119-258`）从 `gepa_result` + `callback_rounds` 组装 `OptimizeResult`
- 阶段 B `_persist_artifacts`（L374-437）写：
  - `output_dir/result.json` — 完整 `OptimizeResult.model_dump_json`
  - `output_dir/summary.txt` — 人类可读摘要
  - `output_dir/rounds/round_NNN.json` — 每轮 `RoundRecord`
  - `output_dir/baseline_prompts/<name>.md`
  - `output_dir/best_prompts/<name>.md`
  - `output_dir/config.snapshot.json`
  - `output_dir/run.log`

### 调用链时序图（一图速览）

```
run_optimization.py
        │
        ▼
AgentOptimizer.optimize(config_path, call_agent, target_prompt, train_path, val_path, output_dir)
        │
        ├─ load_optimize_config  ──► OptimizeConfigFile
        ├─ _validate_inputs
        ├─ target_prompt.read_all  ──► baseline_snapshot
        │
        ├─ GepaReflectiveOptimizer(config, call_agent, target_prompt, ...).run(reporter)
        │       │
        │       ├─ _load_evalset_cases(train_path) + _load_evalset_cases(val_path)
        │       ├─ _AgentGEPAAdapter(target_prompt, eval_config, call_agent, ...)
        │       ├─ _OptimizeModelCallable(algo.reflection_lm)
        │       │
        │       └─ asyncio.to_thread(gepa.optimize, ...)   ◄── GEPA 主循环（每轮）
        │               │
        │               ├─ module_selector 选字段
        │               ├─ adapter.evaluate(parent, train_subsample)
        │               │       │
        │               │       └─ run_evaluator ──► AgentEvaluator.get_executer
        │               │               │
        │               │               └─ _EvalExecuter._run
        │               │                       │
        │               │                       ├─ _load_eval_set_from_file ──► EvalSet
        │               │                       ├─ evaluate_eval_set
        │               │                       │       │
        │               │                       │       └─ _get_eval_results_by_eval_id
        │               │                       │               ├─ perform_inference (call_agent)
        │               │                       │               └─ eval_service.evaluate (metric evaluators)
        │               │                       │                       └─► EvalCaseResult[]
        │               │                       │
        │               │                       └─ (failure) raise _EvaluationCasesFailed
        │               │
        │               ├─ make_reflective_dataset(failed_cases) ──► markdown
        │               ├─ reflection_lm(markdown) ──► candidate prompt
        │               ├─ target_prompt.write_all(candidate)  ◄── 落盘新候选
        │               ├─ adapter.evaluate(candidate, full_valset)
        │               ├─ Pareto 比较 → accept / reject
        │               └─ stop_callbacks 投票
        │
        ├─ (optional) target_prompt.write_all(result.best_prompts)  ◄── update_source=True
        │
        └─ _persist_artifacts
                ├─ result.json / summary.txt
                ├─ rounds/round_NNN.json
                ├─ baseline_prompts/<name>.md
                ├─ best_prompts/<name>.md
                ├─ config.snapshot.json
                └─ run.log
```

---

## 3. evaluation 测试失败的完整 traceback

`tests/evaluation` 共 **2 条**测试失败（来自 `pytest_full.log` L728-744 与 L1047+；完整 suite 9290 通过 / 118 失败，evaluation 模块占 2 条）。

### 失败 1：`test_eval_executer_raises_evaluation_cases_failed_on_case_failure`

```
tests\evaluation\test_optimize_evaluator_call.py:524: in test_eval_executer_raises_evaluation_cases_failed_on_case_failure
    await executer.evaluate()
trpc_agent_sdk\evaluation\_agent_evaluator.py:244: in evaluate
    await self._ensure_run()
trpc_agent_sdk\evaluation\_agent_evaluator.py:236: in _ensure_run
    await self._task
trpc_agent_sdk\evaluation\_agent_evaluator.py:187: in _run
    eval_set = AgentEvaluator._load_eval_set_from_file(test_file, eval_config)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
trpc_agent_sdk\evaluation\_agent_evaluator.py:673: in _load_eval_set_from_file
    raise FileNotFoundError(f"Eval set file not found: {actual_file_path}")
E   FileNotFoundError: Eval set file not found: C
```

**根因**：Windows 路径解析 bug。测试用 `tmp_path / "tiny.evalset.json"` 生成形如 `C:\Users\...\tiny.evalset.json` 的绝对路径。`_load_eval_set_from_file` 在 `_agent_evaluator.py:667` 检查 `if ":" in eval_set_file:` 时假定 `:` 是 ADK 风格的 case 选择分隔符（`file.json:case_id`），用 `split(":", 1)` 取 parts[0] 作为文件路径。Windows 盘符 `C:` 被错误切分，`actual_file_path` 变成 `"C"`，文件查找失败。

**修复方向**（不在本次任务范围）：用 `os.path.splitdrive()` 先剥离盘符再查 `:`，或限制分隔符只匹配 `.json:` 之后的字符。

### 失败 2：`test_quickstart_real_gepa_loop_reuses_single_event_loop_across_rounds`

```
tests\evaluation\test_optimize_quickstart_example.py:475: in test_quickstart_real_gepa_loop_reuses_single_event_loop_across_rounds
    assert len(seen_loop_ids) >= 2, (
E   AssertionError: Expected real gepa main loop to call call_agent more than once; saw 0 call(s).
E   assert 0 >= 2
E    +  where 0 = len([])
```

**根因**：这个测试 monkeypatch `_OptimizeModelCallable.__call__` 用 fake reflection LM，设了 `max_metric_calls=6` / `max_iterations_without_improvement=1`，期待 gepa 主循环至少跑 2 轮（baseline + round 1）。但 `seen_loop_ids` 为空 → call_agent 一次都没被调到。可能原因：

- 该测试依赖 `examples/optimization/quickstart/optimizer.json` 中的 `${TRPC_AGENT_MODEL_NAME}` 等环境变量，未设置时 gepa 在 baseline 评估阶段就抛异常退出，没有进入第一轮反思
- 也可能是 `asyncio.to_thread(gepa_optimize, ...)` 在 worker thread 内调用 `call_agent`（async），而 monkeypatch 的 `stub_call_agent` 没有跨线程传播；或 gepa 的 asyncio event loop 配置在 Windows ProactorEventLoop 下行为不同

### 测试运行说明

本次报告未能成功跑出 `pytest tests/evaluation -q --tb=short` 的新日志：用 PowerShell `Tee-Object` 时不实时刷新；改用 bash `> pytest_eval.log 2>&1` 重定向后，进程运行 16+ 分钟仍未结束（远超完整 suite 的 3.5 分钟），疑似 Windows 下 gepa / asyncio 阻塞或缺 API key 卡住。所有失败数据已从既有的 `pytest_full.log`（完整 suite 运行结果）提取。

---

## 4. #91 新增示例目录建议

任务规格明确要求 `examples/optimization/eval_optimize_loop/`。基于现有 9 个 example 的命名约定与目录结构，**强烈建议直接按规格放在**：

```
examples/optimization/eval_optimize_loop/
├── README.md                           # 300–500 字方案设计说明 + 使用指引
├── pipeline.py                         # 入口脚本（baseline 评测 → 失败归因 → 优化 → 回归 → 决策 → 落盘）
├── optimizer.json                      # GEPA + metric 配置（fake model + trace mode 默认开）
├── train.evalset.json                  # 3 条训练 case
├── val.evalset.json                    # 3 条验证 case（覆盖可优化成功 / 优化无效 / 优化退化的三类场景）
├── agent/
│   ├── __init__.py
│   ├── agent.py                        # create_agent() 工厂，重读 prompt
│   ├── config.py                       # 环境变量读取，支持 FAKE_MODEL=1 短路
│   └── prompts/
│       ├── system.md                   # 被优化的 system prompt
│       └── skill.md                    # 被优化的 skill prompt
├── attribution/                        # 失败归因模块（规格第 2 阶段）
│   └── cluster.py                      # 按失败类型聚类（reply_mismatch / tool_call_error / param_error / rubric_fail / knowledge_fail / format_fail）
├── gate/                               # 接受策略（规格第 5 阶段）
│   └── accept_policy.py                # 可配置 gate：val_improvement ≥ threshold / no_new_hard_fail / critical_case_no_regression / cost ≤ budget
├── audit/                              # 审计落盘（规格第 6 阶段）
│   └── report.py                       # 生成 optimization_report.json + .md
├── fake/                               # fake judge / fake model / trace mode（规格要求）
│   ├── fake_model.py
│   ├── fake_judge.py
│   └── traces/                         # 预录对话，trace mode 用
├── runs/                               # 输出根目录（每次运行写到时间戳子目录）
└── optimization_report.json.example    # 示例输出
```

### 选此位置的理由

1. **目录命名与规格 1:1 对齐**：任务规格交付物明确写 `examples/optimization/eval_optimize_loop/`，与现有 9 个 example 同层级，便于索引
2. **现有目录覆盖场景互补**：现有 9 个 example 都只展示"优化器本身怎么用"，没有任何一个演示"评测 → 归因 → 优化 → 回归 → 决策 → 审计"端到端闭环。`ci_integration/` 最接近但只覆盖 PR 守门 + 夜间优化两段，缺失败归因和 gate decision
3. **依赖现有模块最小**：`pipeline.py` 直接复用 `AgentEvaluator.evaluate` 跑 baseline + 回归，`AgentOptimizer.optimize` 跑优化轮，新增的 `attribution/` `gate/` `audit/` 是薄包装层
4. **fake mode 可对照 quickstart 测试**：trace mode + fake judge 在 `optimizer.json` 里通过 `"judge_model": {"model_name": "fake-judge"}` 触发，`fake/fake_model.py` 提供 `async def fake_call_agent(query)` 直接返回 `val.evalset.json` 中预录的 `final_response`，满足"无 API Key 跑通 ≤3min"的验收标准

### 与现有模块的集成点

- `pipeline.py` 调 `AgentEvaluator.get_executer(val_path, call_agent=fake_call_agent, eval_metrics_path="optimizer.json", print_summary_report=False)` 跑 baseline，拿 `EvaluateResult`
- `attribution/cluster.py` 遍历 `EvaluateResult.results_by_eval_set_id[...].eval_results_by_eval_id` 的每个 `EvalCaseResult`，按 `eval_metric_results[*].metric_name` 与 `details.reason` 分类：
  - `final_response_avg_score` fail → `"reply_mismatch"` 或 `"format_fail"`
  - `tool_trajectory_avg_score` fail → `"tool_call_error"` 或 `"param_error"`
  - `llm_rubric_response` fail → `"rubric_fail"`
  - `llm_rubric_knowledge_recall` fail → `"knowledge_fail"`
- 调 `AgentOptimizer.optimize(config_path="optimizer.json", call_agent=fake_call_agent, target_prompt=target, train_path, val_path, output_dir)` 跑优化
- 拿到 `OptimizeResult` 后，`audit/report.py` 调 `AgentEvaluator.get_executer(val_path, call_agent=...)` 用 `result.best_prompts`（先 `target_prompt.write_all(result.best_prompts)`）再跑一次 val 评测做候选验证
- `gate/accept_policy.py` 比较 baseline 与 candidate 的 `EvaluationOutcome`，输出 `{"accepted": bool, "reason": str}`，写入 `optimization_report.json`

### 6 条 case 的设计要点（满足规格三类场景）

| case_id | 集合 | 场景类型 | 设计方式 |
|---|---|---|---|
| `t_opt_success` | train | 可优化成功 | baseline prompt 漏掉"输出答案前必须给出推理步骤"，导致 final_response_avg_score fail；reflection LM 补上即可通过 |
| `t_opt_noop` | train | 优化无效 | case 本身模糊（缺少关键数字），任何 prompt 都无法让 agent 给出匹配答案；reflection LM 反复改写无法提升 |
| `t_opt_regress` | train | 优化后退化的诱因 | 训练集包含一个特殊格式偏好（例如要求 "答：" 而非 "答案："），改写 prompt 满足此 case 会牺牲验证集 |
| `v_opt_success` | val | 验证可优化成功 | 与 `t_opt_success` 同类型，验证 baseline→candidate 提升 |
| `v_opt_noop` | val | 验证优化无效 | 同 `t_opt_noop`，确认 train 与 val 一致地无提升 |
| `v_opt_regress` | val | 验证退化 | 与 `t_opt_regress` 的格式偏好冲突，candidate 通过 train 但在 val 退化；gate policy 必须拒绝 |

接受策略 gate 至少配置：
- `val_total_score_improvement >= 0.02`（防止噪声波动被误判为提升）
- `no_new_hard_fail = True`（candidate 不能让任何 baseline PASS 的 case 变 FAIL）
- `critical_case_ids = ["v_opt_regress"]` 不能退化（直接命中规格验收标准 3）
- `cost_budget_usd = 5.0`（防止 fake mode 失灵时 LLM 失控）

---

## 附录：关键文件行号速查表

| 关注点 | 文件:行号 |
|---|---|
| AgentEvaluator 主入口 | `trpc_agent_sdk/evaluation/_agent_evaluator.py:255` |
| AgentEvaluator.evaluate 静态方法 | `_agent_evaluator.py:308` |
| AgentEvaluator.get_executer | `_agent_evaluator.py:363` |
| `_EvalExecuter._run` | `_agent_evaluator.py:140` |
| `_EvaluationCasesFailed` 抛出点 | `_agent_evaluator.py:231` |
| Windows 盘符 bug 现场 | `_agent_evaluator.py:667`（`if ":" in eval_set_file`）|
| `evaluate_eval_set` | `_agent_evaluator.py:470` |
| `_get_eval_results_by_eval_id` | `_agent_evaluator.py:815` |
| AgentOptimizer 主入口 | `trpc_agent_sdk/evaluation/_agent_optimizer.py:112` |
| `AgentOptimizer.optimize` | `_agent_optimizer.py:132` |
| `_validate_inputs` | `_agent_optimizer.py:544` |
| `_persist_artifacts` | `_agent_optimizer.py:374` |
| `_mask_sigint` 上下文管理器 | `_agent_optimizer.py:72` |
| `run_evaluator` 桥接函数 | `trpc_agent_sdk/evaluation/_optimize_evaluator_call.py:83` |
| `summarize_outcome` | `_optimize_evaluator_call.py:44` |
| `EvaluationOutcome` 数据类 | `_optimize_evaluator_call.py:23` |
| `GepaReflectiveOptimizer.run` | `trpc_agent_sdk/evaluation/_optimize_gepa_reflective.py:431` |
| `_build_stop_callbacks` | `_optimize_gepa_reflective.py:294` |
| `_build_optimize_result` | `_optimize_gepa_reflective.py:119` |
| `_AgentGEPAAdapter` | `trpc_agent_sdk/evaluation/_optimize_gepa_adapter.py` |
| `OptimizeResult` schema | `trpc_agent_sdk/evaluation/_optimize_result.py:167` |
| `RoundRecord` schema | `_optimize_result.py:43` |
| `TargetPrompt` | `trpc_agent_sdk/evaluation/_target_prompt.py:63` |
| `EvalSet` schema | `trpc_agent_sdk/evaluation/_eval_set.py:33` |
| quickstart 入口 | `examples/optimization/quickstart/run_optimization.py` |
| quickstart agent 工厂 | `examples/optimization/quickstart/agent/agent.py` |
| quickstart optimizer.json | `examples/optimization/quickstart/optimizer.json` |
| quickstart train.evalset.json | `examples/optimization/quickstart/train.evalset.json`（5 条小学算术题）|
| quickstart val.evalset.json | `examples/optimization/quickstart/val.evalset.json`（3 条小学算术题）|

---

## 5. 现有能力盘点（Issue #91 视角）

> 本节回答："SDK 已经提供了哪些能力可以直接复用，不必重造？"——是对第 1、2 节的能力视角重组，按 Issue #91 要求的"评测 / 归因 / 优化 / 验证 / 决策 / 审计"六个阶段归类。

### 5.1 评测阶段（AgentEvaluator）

| 能力 | 关键文件 / 行号 | 是否已就绪 |
|---|---|---|
| 评测入口（有返回值版本） | `_agent_evaluator.py:363` `AgentEvaluator.get_executer(...)` → `_EvalExecuter` → `await executer.evaluate()` → `executer.get_result(): EvaluateResult` | ✅ |
| 评测入口（断言版本，CI 友好） | `_agent_evaluator.py:308` `AgentEvaluator.evaluate(...)`，无返回值，全失败抛 `_EvaluationCasesFailed`（L78-93，`AssertionError` 子类） | ✅ |
| EvalSet JSON schema | `_eval_set.py:33` `EvalSet`；`_eval_case.py:170-239` `EvalCase` / `Invocation` / `IntermediateData` | ✅ |
| trace mode（跳过 agent 直接打分预录对话） | `_eval_case.py:152` `EvalModeTrace = "trace"`；`_agent_evaluator.py:300-530` `_is_trace_only` 分支 | ✅ |
| 7 个内置 metric | `_eval_metrics.py:45-66` `PrebuiltMetrics`：`final_response_avg_score` / `tool_trajectory_avg_score` / `response_match_score` / `response_evaluation_score` / `llm_final_response` / `llm_rubric_response` / `llm_rubric_knowledge_recall` | ✅ |
| Pass/Fail 判定 | `_eval_metrics.py:38-42` `EvalStatus`；每个 evaluator 内部 `_get_eval_status(score) = PASSED if score >= threshold else FAILED` | ✅ |
| Case 级失败原因字段 | `_eval_result.py:185` `EvalCaseResult.error_message`；`:108-116` `EvalMetricResultDetails.reason`；`:77-81` `PerInvocationResult.reason/rubric_scores`；`:53` `NamedScoreResult.reason` | ✅ |
| 工具调用轨迹字段 | `_eval_case.py:121` `Invocation.intermediate_data.tool_uses/tool_responses`；`:242-289` `get_all_tool_calls` / `get_all_tool_responses` | ✅ |

### 5.2 归因阶段（部分已就绪）

| 能力 | 现状 | 缺口 |
|---|---|---|
| 字段级归因（GEPA 已做） | `RoundRecord.optimized_field_names`（`_optimize_result.py:88-91`）；`RoundRecord.per_field_diagnosis`（`:96-99`）来自反思 LM | — |
| 反思 LM 自动看失败 case | GEPA 算法内置（`_optimize_gepa_adapter.py:657-721` `make_reflective_dataset`），`reflection_minibatch_size` 控制批大小 | — |
| **case 级失败类型聚类（6 类）** | 字段都在，但**没有任何示例代码**做"按失败类型聚类"（回复不匹配 / 工具调用错 / 参数错 / rubric 不达标 / 召回不足 / 格式不符） | ❌ 需在 `eval_optimize_loop/` 新增 |

### 5.3 优化阶段（AgentOptimizer）

| 能力 | 关键文件 / 行号 | 是否已就绪 |
|---|---|---|
| 优化入口 | `_agent_optimizer.py:132-296` `AgentOptimizer.optimize(*, config_path, call_agent, target_prompt, train_dataset_path, validation_dataset_path, output_dir, update_source=False, ...)` | ✅ |
| GEPA 反思算法（当前唯一注册） | `_optimize_gepa_reflective.py:408` `GepaReflectiveOptimizer`；adapter 在 `_optimize_gepa_adapter.py:505`；callback 在 `_optimize_gepa_callback.py:81` | ✅ |
| 配置 schema | `_optimize_config.py:226-243` `OptimizeConfigFile`；`GepaReflectiveAlgo` 含 `max_metric_calls` / `max_iterations_without_improvement` / `timeout_seconds` / `score_threshold` / `max_candidate_proposals` / `max_tracked_candidates` 等 6 类停止条件 | ✅ |
| 候选选择策略 | `_optimize_config.py` `candidate_selection_strategy ∈ {pareto, current_best, epsilon_greedy, top_k_pareto}`；`frontier_type ∈ {instance, objective, hybrid, cartesian}` | ✅ |
| 多字段 prompt 优化 | `module_selector ∈ {round_robin, all, random}`；`use_merge=true` 触发字段合并（要求注册 ≥2 字段，`_agent_optimizer.py:602-614`） | ✅ |
| 框架层接受/早停 | `_optimize_gepa_reflective.py:89-116, 294-381` `_RequiredMetricsAboveThresholdStopper` + 6 类 stopper；`optimize.stop.required_metrics` ∈ `"all" / list / []` | ✅ |

### 5.4 验证阶段（部分已就绪）

| 能力 | 现状 | 缺口 |
|---|---|---|
| train / val 物理隔离校验 | `_agent_optimizer.py:544-614` `_validate_inputs` 强制 train≠val | ✅ |
| 每轮全量 val 评估 | GEPA 算法内置（`_optimize_gepa_adapter.py:594-655`） | ✅ |
| 独立 val 评测（脱离 optimizer） | `AgentEvaluator.get_executer(val_path, ...)` 即可，参考 `examples/optimization/ci_integration/tests/test_agent_quality.py:48-62` | ✅ |
| **逐 case 跨 run 回归 diff** | `examples/optimization/advanced_strategies/compare.py:52-96` 只对比汇总指标，**不做 case-level diff** | ❌ 需新增 |

### 5.5 接受决策阶段（部分已就绪）

| 能力 | 现状 | 缺口 |
|---|---|---|
| Pareto 前沿 / 早停 / required_metrics 门禁 | 见 5.3 | ✅ |
| **业务级可配置 gate**（"总分提升≥N"、"hard fail 数不增"、"关键 case 不退化"、"成本≤预算"） | **完全不存在**，SDK 的 stop policy 只控制"早停"，不等价于业务"是否回写" | ❌ 需新增 |

### 5.6 审计阶段（部分已就绪）

| 能力 | 现状 | 缺口 |
|---|---|---|
| `result.json` / `summary.txt` / `rounds/round_NNN.json` | `_agent_optimizer.py:374-491` `_persist_artifacts` | ✅ |
| `baseline_prompts/` + `best_prompts/` 双快照 | 同上 | ✅ |
| 时间戳子目录隔离 | 所有 example 入口都做了，例如 `quickstart/run_optimization.py:147-148` | ✅ |
| `config.snapshot.json` 复现配置 | 同上 | ✅ |
| **`optimization_report.{json,md}`（含 baseline / candidate / 逐 case delta / gate decision / 失败归因 / 成本 / 耗时 / 随机种子）** | **完全不存在** | ❌ 需新增 |
| **操作员级审计日志（谁/何时/改了哪个 prompt）** | **完全不存在**；`update_source=True` 直接覆盖源文件 | ❌ 需新增 |

### 5.7 fake judge / fake model / trace mode

> Issue 验收标准 #5 要求"fake model / trace mode 下完整 pipeline 耗时 ≤ 3 分钟"。

| 路径 | 现状 | 实现策略 |
|---|---|---|
| **trace mode（原生）** | ✅ `_eval_case.py:152` + `_agent_evaluator.py:300-530`；示例 `examples/evaluation/trace_mode/` | **首选**：在 `evalset` 中给 `eval_mode="trace"` + `actual_conversation`，评测阶段完全跳过 agent 推理，Windows 上稳定 ≤3 min |
| **fake call_agent（黑盒）** | ✅ `_agent_optimizer.py:547-551`、`_optimize_evaluator_call.py:83-136`，`CallAgent = async (query: str) -> str` 协议，业务自定义 | 次选：在 `fake/fake_model.py` 实现 async 函数，按规则 / 字典返回字符串 |
| **fake judge（自定义 scorer）** | ✅ `_llm_evaluator.py:103-178` `LLM_EVALUATOR_REGISTRY` 支持注册自定义 `response_scorer` / `models_aggregator`，scorer 内可不调 LLM | 在 `fake/fake_judge.py` 注册假 scorer |
| **fake reflection LM** | ✅ `_optimize_model_callable.py:210-309` 可被 monkeypatch；`OptimizeModelOptions.base_url`（`_optimize_model_options.py:24`）可指向本地 fake server；`ModelRegistry` 也支持自注册 provider | 在 `fake/fake_reflection.py` 包一个符合 gepa `LanguageModel` 协议的同步 callable |
| **框架内置 fake 实现** | ❌ 不存在 | **必须新增** `fake/` 子目录，落地上述四个 fake 路径 |

---

## 6. 需要新增能力清单（仅在 `examples/optimization/eval_optimize_loop/`）

下表把第 5 节标 ❌ 的缺口集中起来，给出建议落点。

| # | 新增能力 | 落点（相对 `eval_optimize_loop/`） | 输入 | 输出 |
|---|---|---|---|---|
| 1 | 失败归因分类器 | `attribution/cluster.py` | `EvalCaseResult[]` + `EvalCase.conversation[*].intermediate_data` | `{category: str, case_ids: list[str], sample_reason: str}[]`，category ∈ {reply_mismatch, tool_call_error, param_error, rubric_fail, knowledge_fail, format_fail} |
| 2 | 逐 case 跨 run regression diff | `regression/diff.py` | baseline `EvaluateResult` + candidate `EvaluateResult` | `{newly_passed, newly_failed, score_up, score_down: list[{eval_id, baseline_score, candidate_score, delta}]}` |
| 3 | 可配置接受 gate | `gate/accept_policy.py` | gate config（JSON）+ regression diff + cost + critical_case_ids | `{accepted: bool, reasons: list[str], violated_rules: list[str]}` |
| 4 | 双格式优化报告 | `audit/report_builder.py` | baseline / candidate 评测结果 + diff + 归因 + gate 决策 + cost + duration + seed | `optimization_report.json` + `optimization_report.md` |
| 5 | 审计落盘增强 | 同 #4，加 `audit` 节 | seed / total_cost / duration / accepted / reasons / repro_config_hash | 写入 `optimization_report.json` 顶层 |
| 6 | fake 实现四件套 | `fake/fake_model.py` / `fake_judge.py` / `fake_reflection.py` / `traces/*.json` | — | 满足"无 API Key 跑通" |
| 7 | 6 条三类样例 case | `data/train.evalset.json`（3 条）+ `data/val.evalset.json`（3 条） | — | 覆盖"可优化成功 / 优化无效 / 优化后退化"三类各 1/3 |
| 8 | pipeline 入口 + README | `run_pipeline.py` + `README.md` | `optimizer.json` + `data/*.json` + `prompts/*.md` | 端到端产物 + 300–500 字设计说明 |

---

## 7. 建议集成点（不修改 SDK 的前提下）

> 这一节回答："新增的 8 个模块怎么和 SDK 现有 API 对接？"

### 7.1 跑 baseline 与 candidate 评测

```python
# 拿 EvaluateResult 对象做 diff，必须用 get_executer，不要用 evaluate（无返回值）
executer = AgentEvaluator.get_executer(
    eval_dataset_path=str(VAL_PATH),
    call_agent=fake_call_agent,                       # 或 trace mode 下传 None
    eval_metrics_file_path_or_dir="optimizer.json",   # 复用同一份 metric 配置
    num_runs=1,
    print_detailed_results=False,
    print_summary_report=False,
)
await executer.evaluate()
baseline_result = executer.get_result()               # → EvaluateResult
```

关键位置：`_agent_evaluator.py:363`（`get_executer`）、`_agent_evaluator.py:140`（`_EvalExecuter._run`）、`_eval_result.py:310`（`EvaluateResult`）。

### 7.2 跑优化（保留自动回滚）

```python
result: OptimizeResult = await AgentOptimizer.optimize(
    config_path="optimizer.json",
    call_agent=fake_call_agent,
    target_prompt=target,                             # TargetPrompt().add_path("system_prompt", ...).add_path("skill", ...)
    train_dataset_path="data/train.evalset.json",
    validation_dataset_path="data/val.evalset.json",
    output_dir="runs/<timestamp>/optimizer",
    update_source=False,                              # ★ 让 SDK 自动回滚 baseline，避免污染下一次评测
    verbose=1,
)
best_prompts: dict[str, str] = result.best_prompts    # 最优候选 prompt
```

关键位置：`_agent_optimizer.py:132-296`、`_optimize_result.py:167`（`OptimizeResult`）、`_target_prompt.py:63`（`TargetPrompt`，**注意：仓库中没有 `TargetSkill` 类，skill 是 `TargetPrompt` 的字段名之一**）。

### 7.3 写回 candidate 重跑验证集

```python
baseline_snapshot = await target.read_all()
try:
    await target.write_all(best_prompts)              # 临时把 candidate 写到源文件
    candidate_executer = AgentEvaluator.get_executer(
        eval_dataset_path=str(VAL_PATH),
        call_agent=fake_call_agent,
        eval_metrics_file_path_or_dir="optimizer.json",
        num_runs=1,
        print_detailed_results=False,
        print_summary_report=False,
    )
    await candidate_executer.evaluate()
    candidate_result = candidate_executer.get_result()
finally:
    await target.write_all(baseline_snapshot)         # ★ 必须复位，否则污染后续 run
```

关键位置：`_target_prompt.py:135-171` `write_all`（path 字段用 tmp+`os.replace` 原子写，部分失败自动回滚）、`_target_prompt.py:128-133` `read_all`。

### 7.4 失败归因输入来源

从 baseline 的 `EvaluateResult` 取：

- `result.results_by_eval_set_id[set_id].eval_results_by_eval_id[eval_id].error_message`（`_eval_result.py:185`）
- `result....eval_metric_results[*].details.reason`（`_eval_result.py:108-116`）
- 对应的 `EvalCase.conversation[*].intermediate_data.tool_uses` / `tool_responses`（`_eval_case.py:121`）—— 用于区分"工具调用错"vs"参数错"

trace mode 下这些字段都已就绪；非 trace 模式需要让 fake call_agent 在 `IntermediateData` 里回填假 tool_uses。

### 7.5 fake 路径选择优先级

1. **首选 trace mode**：评测阶段完全跳过 agent 推理，Windows 上稳定 ≤3 分钟。需要让 train/val evalset 都带 `eval_mode="trace"` + `actual_conversation`。
2. **次选 fake call_agent**：当 case 不易预录对话时，在 `fake/fake_model.py` 实现 async 函数（按规则 / 字典返回字符串），由 pipeline 传入 `AgentEvaluator` / `AgentOptimizer`。
3. **fake judge**：在 `fake/fake_judge.py` 用 `LLM_EVALUATOR_REGISTRY.register_response_scorer(...)`（`_llm_evaluator.py:103-178`）注册假 scorer，让 LLM metric 不调真实 LLM。
4. **fake reflection LM**：在 `fake/fake_reflection.py` 包一个符合 gepa `LanguageModel` 协议的同步 callable，通过 `optimizer.json` 的 `reflection_lm.model_name = "fake-reflection"` 触发，或在 `run_pipeline.py` 里 monkeypatch `_OptimizeModelCallable.__call__`（`_optimize_model_callable.py:245-248`）。

### 7.6 接受 gate 必须独立于 SDK 决策

SDK 的 `stop.required_metrics` 只控制"早停"，不等于业务"是否接受 candidate"。新 pipeline 的 gate 必须放在 `AgentOptimizer.optimize` 返回**之后**再判一次：

```python
result: OptimizeResult = await AgentOptimizer.optimize(...)
# 即使 result.status == "SUCCEEDED"，也要再判业务 gate
gate_decision = accept_policy.evaluate(
    baseline_result=baseline_val_result,
    candidate_result=candidate_val_result,
    regression_diff=diff,
    cost_usd=result.total_cost,
    config=gate_config,
)
if not gate_decision.accepted:
    # 即使 SDK 把 best_prompts 落到 best_prompts/ 也不回写源文件
    pass
```

这是"不能新增 hard fail"、"关键 case 不能退化"、"成本 ≤ 预算"等业务规则的**唯一干净落点**——SDK 内部的 Pareto / threshold 早停都不覆盖这些。

### 7.7 复现配置与随机种子

- `optimizer.json` 中的 `algorithm.seed`（`_optimize_config.py` `GepaReflectiveAlgo.seed`）是 GEPA 随机种子，写进 `audit.seed`
- `config.snapshot.json` 已由 SDK 自动落（`_agent_optimizer.py:425-432`），做 SHA-256 写进 `audit.repro_config_hash`
- 环境变量差异（`TRPC_AGENT_MODEL_NAME` 等）脱敏后写进 `audit.env_diff_redacted`

### 7.8 集成点时序图（一图速览）

```
run_pipeline.py
    │
    ├─ ① baseline 评测
    │   └─ AgentEvaluator.get_executer(val_path, call_agent=fake, ...) → EvaluateResult
    │
    ├─ ② 失败归因
    │   └─ attribution/cluster.py(EvaluateResult) → 归因类别列表
    │
    ├─ ③ 优化
    │   └─ AgentOptimizer.optimize(config_path, call_agent=fake, target_prompt, train, val, output_dir)
    │       │
    │       └─ 内部: GEPA 主循环（每轮：反思 LM → 写候选 → 跑 train/val → Pareto 比较）
    │           └─ 返回 OptimizeResult.best_prompts
    │
    ├─ ④ candidate 评测（写回 + 跑 val + 复位）
    │   ├─ target_prompt.write_all(best_prompts)
    │   ├─ AgentEvaluator.get_executer(val_path, call_agent=fake, ...) → EvaluateResult
    │   └─ finally: target_prompt.write_all(baseline_snapshot)   ◄── 必须复位
    │
    ├─ ⑤ 逐 case regression diff
    │   └─ regression/diff.py(baseline_result, candidate_result) → diff
    │
    ├─ ⑥ 接受 gate（独立于 SDK 决策）
    │   └─ gate/accept_policy.py(diff, cost, config) → {accepted, reasons[]}
    │
    └─ ⑦ 双格式报告 + 审计
        └─ audit/report_builder.py(全部上述产物) → optimization_report.{json,md}
```

---

## 8. 关键决策与陷阱速查

1. **没有 `TargetSkill` 类**——全仓库只有 `TargetPrompt`（`_target_prompt.py:63`），skill 是 `TargetPrompt.add_path("skill", ...)` 的字段名。
2. **框架不内置 fake 实现**——所有 fake 路径都是扩展点（call_agent 协议 / `LLM_EVALUATOR_REGISTRY` / `ModelRegistry` / monkeypatch），必须由 `eval_optimize_loop/fake/` 落地。
3. **trace mode 是 Windows ≤3 min 的最优解**——评测阶段完全跳过 agent，避免 `asyncio.to_thread` + Windows ProactorEventLoop 的潜在阻塞（参考第 3 节失败 #2）。
4. **`AgentEvaluator.evaluate` 无返回值，`get_executer` 才有返回值**——做 regression diff 必须用后者。
5. **`update_source=False` 是安全默认**——SDK 会自动把 baseline 回滚到源文件；pipeline 内不要再额外写回。
6. **接受 gate 必须独立于 SDK**——SDK 的 `stop.required_metrics` / Pareto 早停只控制迭代终止，不等于业务接受。
7. **Windows 盘符 bug 待避**（`_agent_evaluator.py:667`，参考第 3 节失败 #1）——evalset 路径优先用相对路径或 `Path.as_posix()`，绕开 `:` 分隔符误判。
8. **6 条 case 的"退化"场景构造**——让 train 含特殊格式偏好（例如要求 "答：" 而非 "答案："），val 的 expected 与该偏好冲突；candidate 为通过 train 会牺牲 val，gate 必须拒绝。这是覆盖 Issue 验收标准 #3 的关键设计。
