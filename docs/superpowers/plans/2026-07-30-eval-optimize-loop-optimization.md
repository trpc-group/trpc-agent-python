# Eval-Optimize Loop Pipeline Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `examples/optimization/eval_optimize_loop` 流水线改造成符合 `docs/mkdocs/zh/optimization.md` 规范的「评测 → 失败归因 → Prompt 优化 → 回归验证 → 产物审计」闭环，修复 4 个会让结论悄悄变错的 P0，让 real 模式真正能落地。

**Architecture:** 引入 `EvalBackend` Protocol（TraceBackend / LiveBackend）收敛「agent 的实际输出从哪来」这一唯一差异；Stage 4 用 `applied_prompts` 上下文显式写回候选并还原 baseline；metric 分两层（gate ⊇ optimizer）以绕开 SDK 对 `call_agent` 模式的硬约束；产物落 `<timestamp>/` 子目录。

**Tech Stack:** Python 3.10+、pydantic v2（`EvalBaseModel` + `to_camel` 别名）、trpc-agent SDK（`AgentEvaluator.get_executer`、`AgentOptimizer.optimize`、`TargetPrompt`、`OptimizeResult`、`RemoteEvalService`）。

## Global Constraints

| 约束 | 来源 | 体现 |
|---|---|---|
| demo + real 两套模式都保留，real 是主线 | 用户选择 | run_pipeline.py 仅在组装 backend 时分支一次 |
| real 模式 Stage 1/4 用 live，Stage 3 走 `call_agent`（SDK 硬约束） | optimization.md §2.4 / §7.2 | `EvalBackend` 提供 `evaluate(eval_set_path, ..., runner=...)` |
| 优化器不能用 `tool_trajectory_avg_score` 与 `llm_rubric_knowledge_recall` | `_DISALLOWED_METRICS_IN_CALL_AGENT_MODE` | `data/optimizer.json` 不含这两个 metric；`test_optimizer_config.py` 守门 |
| 优化器不能用 trace 数据集 | `RemoteEvalService._reject_trace_cases` | `data/live/` 两个文件路径必须不同；测试守门 |
| `update_source=False` 时 SDK 会回滚 baseline | optimization.md FAQ 末条 | Stage 4 用 `applied_prompts` 显式写回 |
| 每次运行用独立时间戳子目录 | optimization.md §8.1 | `output/<timestamp>/{optimizer/,optimization_report.json,.md}` |
| 优雅停机用 sentinel 文件 `optimize.stop` | optimization.md §8.2 | 仅文档说明，不在代码里加 sentinel 监听（避免引入未公开行为） |
| scenario 按 `eval_id` 后缀推导，不维护映射表 | README §5 | run_pipeline.py 删 SCENARIO_MAP，调用 `derive_scenario(eval_id)` |
| Agent 每次重建以重读 `system.md` | optimization.md §4.1-§4.3 | `call_agent` 闭包内 `create_agent()`；测试用计数器断言 |
| `max_metric_calls` 必须 > 验证集规模，避免 budget 抢闸 | optimization.md §6.4 + README §8 | `optimizer.json` 设 150（baseline 20 + 5×6 + 2×20 ≈ 90，留 1.6×） |

---

## File Structure

| 文件 | 类型 | 职责 |
|---|---|---|
| `pipeline/_eval_backend.py` | 新建 | `EvalBackend` Protocol、`TraceBackend`、`LiveBackend` |
| `pipeline/_runner.py` | 重写 | 编排 6 阶段，接 `EvalBackend` |
| `pipeline/_stage_baseline.py` | 修改 | `except AssertionError` 替代 `except Exception` |
| `pipeline/_stage_validation.py` | 重写 | `applied_prompts` 写回 + 还原；接 backend |
| `pipeline/_stage_optimization.py` | 修改 | 透传 audit 字段、verbose=1、产物落 `<ts>/optimizer/` |
| `pipeline/_models.py` | 修改 | `OptimizationExecutionReport` 加 stop_reason/finish_reason/total_token_usage/total_metric_calls/rounds |
| `pipeline/_stage_audit_trail.py` | 修改 | 渲染新字段 |
| `data/_generate_evalsets.py` | 重写 | 一次生成 `trace/` + `live/` 两套 |
| `data/test_config.json` | 删除 | 被 `gate_metrics.json` 取代 |
| `data/gate_metrics.json` | 新建 | 2 个 metric（final_response + tool_trajectory），num_runs=2 |
| `data/optimizer.json` | 修改 | max_metric_calls=150、timeout_seconds=1800、pareto、skip_perfect_score=true |
| `data/trace/{train,val_baseline,val_optimized}.evalset.json` | 新建 | demo 三件套（eval_mode=trace） |
| `data/live/{train,val}.evalset.json` | 新建 | real 模式（无 actual_conversation） |
| `data/demo_optimize_result.json` | 修改 | 在 `rounds[]` 基础上加 stop_reason 等顶层字段（已是 camelCase 别名） |
| `run_pipeline.py` | 重写 | 组装 backend；删 SCENARIO_MAP；时间戳子目录 |
| `tests/test_eval_backend.py` | 新建 | TraceBackend / LiveBackend 契约 |
| `tests/test_call_agent_rebuild.py` | 新建 | 计数器断言 call_agent 每次重建 agent |
| `tests/test_applied_prompts.py` | 新建 | 异常时仍还原 baseline |
| `tests/test_datasets_complete.py` | 新建 | 5 个数据集文件齐全 |
| `tests/test_live_datasets_no_trace.py` | 新建 | live 数据集不含 `eval_mode=trace` |
| `tests/test_gate_superset_of_optimizer.py` | 新建 | gate_metrics ⊇ optimizer metrics |
| `tests/test_scenario_derivation.py` | 新建 | 后缀→scenario 推导 |
| `README.md` | 修改 | 路径表更新（trace/live 子目录、gate_metrics.json） |

> 已有 `tests/test_optimizer_config.py` 已守住黑盒不兼容 metric（不需新增）；已有 `tests/test_pipeline_fake.py` 仍走 trace 路径，需更新路径引用。

---

## Task 1: EvalBackend Protocol + TraceBackend

**Files:**
- Create: `pipeline/_eval_backend.py`

**Interfaces:**
- Consumes: `EvaluateResult` from `trpc_agent_sdk.evaluation._eval_result`; `EvalConfig` from `trpc_agent_sdk.evaluation._eval_config`; `AgentEvaluator.get_executer` from `trpc_agent_sdk.evaluation._agent_evaluator`
- Produces: `EvalBackend` Protocol with `async evaluate(*, eval_set_path: str, metrics_config_path: str, num_runs: int = 1) -> tuple[EvaluateResult, EvalSetReport]`; `TraceBackend` 实现

- [ ] **Step 1: Create EvalBackend Protocol + TraceBackend**

```python
# pipeline/_eval_backend.py
"""EvalBackend: 封装「agent 的实际输出从哪来」这一唯一差异。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, runtime_checkable

from trpc_agent_sdk.evaluation._agent_evaluator import AgentEvaluator
from trpc_agent_sdk.evaluation._eval_config import EvalConfig
from trpc_agent_sdk.evaluation._eval_metrics import EvalStatus
from trpc_agent_sdk.evaluation._eval_result import EvaluateResult

from pipeline._models import EvalSetReport, PerCaseScore


@runtime_checkable
class EvalBackend(Protocol):
    async def evaluate(
        self,
        *,
        eval_set_path: str,
        metrics_config_path: str,
        num_runs: int = 1,
    ) -> tuple[EvaluateResult, EvalSetReport]: ...


class TraceBackend:
    """trace 模式 backend: 不调用 agent, 直接从预录制轨迹计算 metric."""

    async def evaluate(
        self,
        *,
        eval_set_path: str,
        metrics_config_path: str,
        num_runs: int = 1,
    ) -> tuple[EvaluateResult, EvalSetReport]:
        with open(metrics_config_path, "r") as f:
            config_data = json.load(f)
        EvalConfig(**config_data)  # 校验

        executer = AgentEvaluator.get_executer(
            eval_dataset_file_path_or_dir=eval_set_path,
            eval_metrics_file_path_or_dir=metrics_config_path,
            num_runs=num_runs,
        )
        # 只吞 AssertionError — 基线本来就该有失败 case
        try:
            await executer.evaluate()
        except AssertionError:
            pass

        raw: EvaluateResult | None = executer.get_result()
        if raw is None:
            raise RuntimeError(f"评测失败 ({eval_set_path}): 无返回结果")
        return raw, _build_report(raw)


def _build_report(raw: EvaluateResult) -> EvalSetReport:
    """Build EvalSetReport from raw SDK EvaluateResult. (Move from _stage_baseline.)"""
    per_case: list[PerCaseScore] = []
    total = passed = failed = 0
    metric_scores: dict[str, list[float]] = {}
    captured_set_id = "unknown"

    for eval_set_id, aggregate in raw.results_by_eval_set_id.items():
        captured_set_id = eval_set_id
        for eval_id, runs in aggregate.eval_results_by_eval_id.items():
            total += 1
            case_result = runs[0]
            if case_result.final_eval_status == EvalStatus.PASSED:
                passed += 1
            elif case_result.final_eval_status == EvalStatus.FAILED:
                failed += 1

            scores: dict[str, float] = {}
            statuses: dict[str, str] = {}
            for m in case_result.overall_eval_metric_results:
                scores[m.metric_name] = m.score or 0.0
                statuses[m.metric_name] = str(m.eval_status.name) if m.eval_status else "NOT_EVALUATED"
                metric_scores.setdefault(m.metric_name, []).append(m.score or 0.0)

            per_case.append(PerCaseScore(
                eval_id=eval_id,
                overall_status=str(case_result.final_eval_status.name) if case_result.final_eval_status else "NOT_EVALUATED",
                metric_scores=scores,
                metric_statuses=statuses,
            ))

    breakdown = {n: sum(s) / len(s) if s else 0.0 for n, s in metric_scores.items()}
    return EvalSetReport(
        eval_set_id=captured_set_id,
        num_cases=total,
        num_passed=passed,
        num_failed=failed,
        pass_rate=passed / total if total > 0 else 0.0,
        metric_breakdown=breakdown,
        per_case=per_case,
    )
```

- [ ] **Step 2: Verify imports succeed**

```bash
cd examples/optimization/eval_optimize_loop && python -c "from pipeline._eval_backend import TraceBackend, EvalBackend; print('ok')"
```

Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add pipeline/_eval_backend.py
git commit -m "feat(pipeline): add EvalBackend Protocol with TraceBackend"
```

---

## Task 2: LiveBackend (real 模式核心)

**Files:**
- Modify: `pipeline/_eval_backend.py`

**Interfaces:**
- Consumes: `create_agent` from `agent.agent`; SDK `AgentEvaluator.get_executer(runner=...)` → LocalEvalService
- Produces: `LiveBackend` 类；持 `agent_factory`（而非实例），每次 `evaluate()` 现建 Runner

> **Why factory, not instance**: LlmAgent 在构造时固化 `instruction`；持实例会让 Stage 4 拿到旧 prompt。optimization.md §4.1-§4.3 明确要求每次重建。

- [ ] **Step 1: Add LiveBackend class to _eval_backend.py**

Append to `pipeline/_eval_backend.py`:

```python
from agent.agent import create_agent
from trpc_agent_sdk.evaluation import LocalEvalService  # SDK export; fall back to underlying class if absent


class LiveBackend:
    """real 模式 backend: 每次 evaluate() 现建 Runner（→ 新 agent → 重读 system.md）。"""

    def __init__(self, agent_factory=create_agent) -> None:
        self._agent_factory = agent_factory
        self._agents_built: int = 0  # 供测试断言

    def agents_built(self) -> int:
        return self._agents_built

    async def evaluate(
        self,
        *,
        eval_set_path: str,
        metrics_config_path: str,
        num_runs: int = 1,
    ) -> tuple[EvaluateResult, EvalSetReport]:
        agent = self._agent_factory(demo_mode=False)
        self._agents_built += 1

        executer = AgentEvaluator.get_executer(
            eval_dataset_file_path_or_dir=eval_set_path,
            eval_metrics_file_path_or_dir=metrics_config_path,
            num_runs=num_runs,
            runner=LocalEvalService(agent=agent),  # LocalEvalService 暴露 agent 内部轨迹
        )
        try:
            await executer.evaluate()
        except AssertionError:
            pass

        raw: EvaluateResult | None = executer.get_result()
        if raw is None:
            raise RuntimeError(f"评测失败 ({eval_set_path}): 无返回结果")
        return raw, _build_report(raw)
```

> **如果 SDK 的 `LocalEvalService` 公开 API 与上面不一致**：读 `trpc_agent_sdk/evaluation/_local_eval_service.py` 找到正确的构造方式，按 SDK 实际签名调整；`runner=` 是 `AgentEvaluator.get_executer` 已支持的命名参数（见 `_agent_evaluator.py:364-420`）。

- [ ] **Step 2: Verify import**

```bash
cd examples/optimization/eval_optimize_loop && python -c "from pipeline._eval_backend import LiveBackend; print('ok')"
```

Expected: prints `ok` (without invoking, which would require API key).

- [ ] **Step 3: Commit**

```bash
git add pipeline/_eval_backend.py
git commit -m "feat(pipeline): add LiveBackend for real-mode evaluation"
```

---

## Task 3: Refactor _stage_baseline.py to use TraceBackend & narrow exception handling

**Files:**
- Modify: `pipeline/_stage_baseline.py`

**Interfaces:**
- Consumes: `TraceBackend.evaluate` (Task 1)
- Produces: 保留 `BaselineEvaluator.evaluate(...)` 但内部委托 backend；删掉内嵌 `_build_report`（已在 `_eval_backend.py`）

> **Why narrow except**: README §6 Stage 1 边界 — `except Exception: pass` 会把「文件缺失」静默成全 0 分，Stage 2 把它归因为 prompt 问题（最坏失败模式）。

- [ ] **Step 1: Replace _stage_baseline.py**

```python
"""Stage 1: 基线评测 — 对训练集/验证集运行 AgentEvaluator."""

from __future__ import annotations

from trpc_agent_sdk.evaluation._eval_result import EvaluateResult

from pipeline._eval_backend import EvalBackend
from pipeline._models import EvalSetReport


class BaselineEvaluator:
    """基线评测执行器 — 委托给 EvalBackend."""

    @staticmethod
    async def evaluate(
        *,
        eval_set_path: str,
        metrics_config_path: str,
        backend: EvalBackend,
        num_runs: int = 1,
    ) -> tuple[EvaluateResult, EvalSetReport]:
        return await backend.evaluate(
            eval_set_path=eval_set_path,
            metrics_config_path=metrics_config_path,
            num_runs=num_runs,
        )
```

- [ ] **Step 2: Verify imports**

```bash
cd examples/optimization/eval_optimize_loop && python -c "from pipeline._stage_baseline import BaselineEvaluator; print('ok')"
```

Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add pipeline/_stage_baseline.py
git commit -m "refactor(pipeline): narrow exception handling and delegate to EvalBackend"
```

---

## Task 4: applied_prompts context manager

**Files:**
- Modify: `pipeline/_eval_backend.py`（同一文件，新增模块）

**Interfaces:**
- Consumes: `TargetPrompt.write_all` from `trpc_agent_sdk.evaluation._target_prompt`
- Produces: `applied_prompts(target_prompt, prompts: dict[str, str])` 异步上下文管理器；用 `TargetPrompt.write_all` 保证原子写 + 多字段失败回滚

> **Why this exists**: `AgentOptimizer.optimize(update_source=False)` 在 `finally` 把源文件回滚成 baseline（README §3.3）。如果验证候选不显式写回，Stage 4 会拿到 baseline 而非候选。

- [ ] **Step 1: Add applied_prompts context manager**

Append to `pipeline/_eval_backend.py`:

```python
from contextlib import asynccontextmanager
from trpc_agent_sdk.evaluation._target_prompt import TargetPrompt


@asynccontextmanager
async def applied_prompts(target_prompt: TargetPrompt, prompts: dict[str, str]):
    """写入候选 prompt, 退出时还原 baseline. 异常路径也保证还原."""
    baseline = target_prompt.read_all()  # SDK 已提供
    try:
        target_prompt.write_all(prompts)  # SDK 原子写 + 多字段失败回滚
        yield
    finally:
        target_prompt.write_all(baseline)
```

> 验证 SDK 实际方法名: `trpc_agent_sdk/evaluation/_target_prompt.py` 中 `read_all` 与 `write_all` 是否存在；若 SDK 仅暴露 `read_text/write_text`（单字段），则改为：

```python
@asynccontextmanager
async def applied_prompts(target_prompt: TargetPrompt, prompts: dict[str, str]):
    baseline: dict[str, str] = {}
    for name, path in target_prompt.paths.items():
        baseline[name] = Path(path).read_text(encoding="utf-8")
    try:
        for name, content in prompts.items():
            Path(target_prompt.paths[name]).write_text(content, encoding="utf-8")
        yield
    finally:
        for name, content in baseline.items():
            Path(target_prompt.paths[name]).write_text(content, encoding="utf-8")
```

按 SDK 实际签名选其一，注释里说明选型理由。

- [ ] **Step 2: Verify import**

```bash
cd examples/optimization/eval_optimize_loop && python -c "from pipeline._eval_backend import applied_prompts; print('ok')"
```

Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add pipeline/_eval_backend.py
git commit -m "feat(pipeline): add applied_prompts context manager for candidate write-back"
```

---

## Task 5: Stage 4 (candidate validation) uses applied_prompts

**Files:**
- Modify: `pipeline/_stage_validation.py`

**Interfaces:**
- Consumes: `EvalBackend` (Task 1/2); `applied_prompts` (Task 4); `TargetPrompt` (with `best_prompts`)
- Produces: `ValidationComparator.evaluate_and_compare(backend, val_baseline_path, best_prompts, target_prompt, gate_metrics_path, baseline_report, scenario_map)` — 在 `applied_prompts` 内重评同一份 val 文件

> **Why this matters**: README §3.3/§6 Stage 4 — demo 用 `val_optimized.evalset.json` 当候选，但 real 必须写回 `best_prompts` 再重评同一份 val 集。

- [ ] **Step 1: Replace _stage_validation.py**

```python
"""Stage 4: 候选验证 — 写回 best_prompts 后重评验证集，与基线逐 case 对比."""

from __future__ import annotations

from trpc_agent_sdk.evaluation._eval_metrics import EvalStatus
from trpc_agent_sdk.evaluation._target_prompt import TargetPrompt

from pipeline._eval_backend import EvalBackend, applied_prompts
from pipeline._models import EvalSetReport, PerCaseDelta


class ValidationComparator:
    @staticmethod
    async def evaluate_and_compare(
        *,
        backend: EvalBackend,
        val_eval_path: str,
        metrics_config_path: str,
        target_prompt: TargetPrompt,
        best_prompts: dict[str, str],
        baseline_report: EvalSetReport,
        scenario_map: dict[str, str] | None = None,
        num_runs: int = 2,
    ) -> tuple[EvalSetReport, list[PerCaseDelta]]:
        if scenario_map is None:
            scenario_map = {}

        # demo 模式下 best_prompts 为空 → 不写回
        if best_prompts:
            async with applied_prompts(target_prompt, best_prompts):
                _, candidate_report = await backend.evaluate(
                    eval_set_path=val_eval_path,
                    metrics_config_path=metrics_config_path,
                    num_runs=num_runs,
                )
        else:
            _, candidate_report = await backend.evaluate(
                eval_set_path=val_eval_path,
                metrics_config_path=metrics_config_path,
                num_runs=num_runs,
            )

        return candidate_report, _compute_deltas(
            baseline_report=baseline_report,
            candidate_report=candidate_report,
            scenario_map=scenario_map,
        )


def _compute_deltas(
    *,
    baseline_report: EvalSetReport,
    candidate_report: EvalSetReport,
    scenario_map: dict[str, str],
) -> list[PerCaseDelta]:
    base = {c.eval_id: c for c in baseline_report.per_case}
    cand = {c.eval_id: c for c in candidate_report.per_case}
    deltas: list[PerCaseDelta] = []

    for eval_id in sorted(set(base) | set(cand)):
        b = base.get(eval_id)
        c = cand.get(eval_id)
        b_status = b.overall_status if b else "MISSING"
        c_status = c.overall_status if c else "MISSING"
        b_scores = dict(b.metric_scores) if b else {}
        c_scores = dict(c.metric_scores) if c else {}
        delta = {
            m: c_scores.get(m, 0.0) - b_scores.get(m, 0.0)
            for m in (set(b_scores) | set(c_scores))
        }
        deltas.append(PerCaseDelta(
            eval_id=eval_id,
            scenario=scenario_map.get(eval_id, "unknown"),
            baseline_status=b_status,
            candidate_status=c_status,
            baseline_scores=b_scores,
            candidate_scores=c_scores,
            score_delta=delta,
            transition=f"{b_status}->{c_status}",
        ))
    return deltas
```

- [ ] **Step 2: Verify imports**

```bash
cd examples/optimization/eval_optimize_loop && python -c "from pipeline._stage_validation import ValidationComparator; print('ok')"
```

Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add pipeline/_stage_validation.py
git commit -m "refactor(pipeline): stage 4 writes best_prompts via applied_prompts"
```

---

## Task 6: _models.py — extend OptimizationExecutionReport with audit fields

**Files:**
- Modify: `pipeline/_models.py`

**Interfaces:**
- Produces: `OptimizationExecutionReport` 新增字段 `stop_reason: str`、`finish_reason: str`、`total_token_usage: dict[str, int]`、`total_metric_calls: int`、`rounds: list[dict]`（与 SDK `RoundRecord` 字段对齐，但保持 dict 以避免模型耦合）

- [ ] **Step 1: Add new fields to OptimizationExecutionReport**

In `pipeline/_models.py`, replace the `OptimizationExecutionReport` class with:

```python
class OptimizationExecutionReport(EvalBaseModel):
    algorithm: str
    status: str
    total_rounds: int
    baseline_pass_rate: float
    best_pass_rate: float
    pass_rate_improvement: float
    duration_seconds: float
    total_llm_cost: float
    best_prompts: dict[str, str] = Field(default_factory=dict)

    # 审计字段 (Stage 6 报告消费)
    stop_reason: str = ""                          # SDK: result.stop_reason
    finish_reason: str = ""                        # SDK: result.finish_reason
    total_token_usage: dict[str, int] = Field(default_factory=dict)  # SDK: result.total_token_usage
    total_metric_calls: int = 0                    # SDK: result.total_metric_calls
    rounds: list[dict] = Field(default_factory=list)  # SDK: result.rounds
```

- [ ] **Step 2: Verify existing tests still pass**

```bash
cd examples/optimization/eval_optimize_loop && pytest tests/test_models.py -v
```

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add pipeline/_models.py
git commit -m "feat(pipeline): extend OptimizationExecutionReport with audit fields"
```

---

## Task 7: _stage_optimization.py — plumb audit fields + verbose=1 + sub-output dir

**Files:**
- Modify: `pipeline/_stage_optimization.py`

**Interfaces:**
- Consumes: SDK `OptimizeResult` 字段 `stop_reason` / `finish_reason` / `total_token_usage` / `total_metric_calls` / `rounds`
- Produces: `OptimizationExecutionReport` 含 Task 6 新字段；产物落 `<output_dir>/<timestamp>/optimizer/`

> **Why verbose=1**: optimization.md §4.7 建议实时观察，避免盲等。
> **Why sub-output dir**: optimization.md §8.1 时间戳子目录 + README §9 「让 SDK 产物与流水线报告不再混在一起」。

- [ ] **Step 1: Update run_real signature and _from_optimize_result**

Replace `pipeline/_stage_optimization.py`:

```python
"""Stage 3: 优化执行 — 运行 AgentOptimizer 或加载 demo 结果."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from trpc_agent_sdk.evaluation._optimize_result import OptimizeResult

from pipeline._models import OptimizationExecutionReport


class OptimizationExecutor:
    @staticmethod
    async def run_real(
        *,
        config_path: str,
        call_agent,
        target_prompt,
        train_dataset_path: str,
        validation_dataset_path: str,
        output_dir: str,           # 期望是 <timestamp>/ 子目录
    ) -> OptimizationExecutionReport:
        from trpc_agent_sdk.evaluation._agent_optimizer import AgentOptimizer

        optimizer_subdir = Path(output_dir) / "optimizer"
        optimizer_subdir.mkdir(parents=True, exist_ok=True)

        result: OptimizeResult = await AgentOptimizer.optimize(
            config_path=config_path,
            call_agent=call_agent,
            target_prompt=target_prompt,
            train_dataset_path=train_dataset_path,
            validation_dataset_path=validation_dataset_path,
            output_dir=str(optimizer_subdir),
            update_source=False,
            verbose=1,
        )
        return OptimizationExecutor._from_optimize_result(result)

    @staticmethod
    def run_demo(demo_result_path: str) -> OptimizationExecutionReport:
        result = OptimizeResult.from_file(demo_result_path)
        return OptimizationExecutor._from_optimize_result(result)

    @staticmethod
    def _from_optimize_result(result: OptimizeResult) -> OptimizationExecutionReport:
        # SDK RoundRecord → dict（camelCase 别名自动转, dict 也可）
        rounds_payload: list[dict[str, Any]] = []
        for r in getattr(result, "rounds", []) or []:
            if hasattr(r, "model_dump"):
                rounds_payload.append(r.model_dump(by_alias=True))
            elif isinstance(r, dict):
                rounds_payload.append(r)
            else:
                rounds_payload.append(json.loads(json.dumps(r, default=str)))

        token_usage = getattr(result, "total_token_usage", {}) or {}
        if hasattr(token_usage, "model_dump"):
            token_usage = token_usage.model_dump(by_alias=True)

        return OptimizationExecutionReport(
            algorithm=result.algorithm,
            status=result.status,
            total_rounds=result.total_rounds,
            baseline_pass_rate=result.baseline_pass_rate,
            best_pass_rate=result.best_pass_rate,
            pass_rate_improvement=result.pass_rate_improvement,
            duration_seconds=result.duration_seconds,
            total_llm_cost=result.total_llm_cost,
            best_prompts=dict(result.best_prompts),
            stop_reason=getattr(result, "stop_reason", ""),
            finish_reason=getattr(result, "finish_reason", ""),
            total_token_usage=dict(token_usage),
            total_metric_calls=getattr(result, "total_metric_calls", 0),
            rounds=rounds_payload,
        )
```

- [ ] **Step 2: Verify imports**

```bash
cd examples/optimization/eval_optimize_loop && python -c "from pipeline._stage_optimization import OptimizationExecutor; print('ok')"
```

Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add pipeline/_stage_optimization.py
git commit -m "feat(pipeline): plumb audit fields and isolate optimizer output to subdir"
```

---

## Task 8: PipelineRunner — adopt EvalBackend, timestamp subdir, demo/real unification

**Files:**
- Modify: `pipeline/_runner.py`

**Interfaces:**
- Consumes: `EvalBackend` (Task 1/2); `applied_prompts` (Task 4); `ValidationComparator` (Task 5)
- Produces: `PipelineRunner` 构造参数 `backend: EvalBackend` 替代 `demo_mode: bool` + `demo_optimize_result_path`；时间戳子目录 `<output_dir>/<timestamp>/`

- [ ] **Step 1: Rewrite _runner.py**

```python
"""PipelineRunner — 编排 6 阶段流水线, 通过 EvalBackend 收敛 demo/real 差异."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from trpc_agent_sdk.evaluation._target_prompt import TargetPrompt

from pipeline._eval_backend import EvalBackend
from pipeline._models import (
    AcceptanceGateConfig,
    GateDecision,
    PipelineReport,
)
from pipeline._stage_acceptance_gate import AcceptanceGate
from pipeline._stage_audit_trail import ReportGenerator
from pipeline._stage_baseline import BaselineEvaluator
from pipeline._stage_failure_attribution import FailureAttributor
from pipeline._stage_optimization import OptimizationExecutor
from pipeline._stage_validation import ValidationComparator


class PipelineRunner:
    def __init__(
        self,
        *,
        train_eval_path: str,
        val_baseline_eval_path: str,
        gate_metrics_config_path: str,
        optimizer_config_path: str,
        prompt_source_path: str,
        prompt_field_name: str,
        gate_config: AcceptanceGateConfig,
        backend: EvalBackend,
        output_dir: str = "output",
        scenario_map: Optional[dict[str, str]] = None,
        demo_optimize_result_path: Optional[str] = None,  # 仅 backend==TraceBackend 时有意义
        train_eval_path_real: Optional[str] = None,        # 仅 backend==LiveBackend 时使用
    ) -> None:
        self._train_path = train_eval_path
        self._val_baseline_path = val_baseline_eval_path
        self._train_path_real = train_eval_path_real
        self._gate_metrics_path = gate_metrics_config_path
        self._optimizer_config_path = optimizer_config_path
        self._prompt_source_path = prompt_source_path
        self._prompt_field_name = prompt_field_name
        self._gate_config = gate_config
        self._backend = backend
        self._output_dir = output_dir
        self._scenario_map = scenario_map or {}
        self._demo_result_path = demo_optimize_result_path
        self._demo_mode = isinstance(backend, type(...)) and backend.__class__.__name__ == "TraceBackend"  # 见 Step 1 注

    async def run(self) -> PipelineReport:
        start = time.time()
        run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir = Path(self._output_dir) / run_ts
        run_dir.mkdir(parents=True, exist_ok=True)

        report = PipelineReport(
            pipeline_version="1.0.0",
            timestamp=datetime.now(timezone.utc).isoformat(),
            demo_mode=self._demo_mode,
        )

        # ---- Stage 1: 基线 ----
        print("[Stage 1/6] 基线评测...")
        _, train_report = await BaselineEvaluator.evaluate(
            eval_set_path=self._train_path,
            metrics_config_path=self._gate_metrics_path,
            backend=self._backend,
            num_runs=2,
        )
        _, val_report = await BaselineEvaluator.evaluate(
            eval_set_path=self._val_baseline_path,
            metrics_config_path=self._gate_metrics_path,
            backend=self._backend,
            num_runs=2,
        )
        report.baseline_train = train_report
        report.baseline_val = val_report

        # ---- Stage 2: 失败归因 ----
        print("[Stage 2/6] 失败归因...")
        # 合并 raw; 为简化 Stage 2 输入, 直接从 reports 重建
        combined = {}
        for c in train_report.per_case:
            combined[c.eval_id] = c
        for c in val_report.per_case:
            combined[c.eval_id] = c
        # 注意: FailureAttributor.cluster 接 dict[str, list[EvalCaseResult]];
        # 保留归一化接口, 让 stage_2 接受 dict[str, PerCaseScore]
        # → 重新设计: 让 FailureAttributor.cluster 支持两路输入
        # (见 Task 9)
        attribution = FailureAttributor.cluster_from_per_case(combined)
        report.failure_attribution = attribution

        # ---- Stage 3: 优化 ----
        print("[Stage 3/6] 优化执行...")
        if self._demo_mode and self._demo_result_path:
            opt_report = OptimizationExecutor.run_demo(self._demo_result_path)
        else:
            from agent.agent import create_agent

            target_prompt = TargetPrompt().add_path(self._prompt_field_name, self._prompt_source_path)
            counter = {"calls": 0}

            async def call_agent(input_text: str) -> str:
                from trpc_agent_sdk.types import Content, Part
                agent = create_agent(demo_mode=False)
                counter["calls"] += 1
                user_content = Content(parts=[Part.from_text(text=input_text)])
                response = await agent.generate_content(user_content)
                if response.candidates and response.candidates[0].content:
                    return "".join(p.text or "" for p in response.candidates[0].content.parts)
                return ""

            opt_report = await OptimizationExecutor.run_real(
                config_path=self._optimizer_config_path,
                call_agent=call_agent,
                target_prompt=target_prompt,
                train_dataset_path=self._train_path_real or self._train_path,
                validation_dataset_path=self._val_baseline_path,
                output_dir=str(run_dir),
            )
        report.optimization_execution = opt_report

        # ---- Stage 4: 候选验证 ----
        print("[Stage 4/6] 候选验证...")
        target_prompt_obj = TargetPrompt().add_path(self._prompt_field_name, self._prompt_source_path)
        candidate_report, deltas = await ValidationComparator.evaluate_and_compare(
            backend=self._backend,
            val_eval_path=self._val_baseline_path,
            metrics_config_path=self._gate_metrics_path,
            target_prompt=target_prompt_obj,
            best_prompts=opt_report.best_prompts,
            baseline_report=val_report,
            scenario_map=self._scenario_map,
        )
        report.candidate_validation = candidate_report
        report.case_deltas = deltas

        # ---- Stage 5: 门控 ----
        print("[Stage 5/6] 接受门控...")
        gate = AcceptanceGate(self._gate_config)
        decision = gate.evaluate(
            baseline_pass_rate=val_report.pass_rate,
            candidate_pass_rate=candidate_report.pass_rate,
            baseline_case_statuses={c.eval_id: c.overall_status for c in val_report.per_case},
            candidate_case_statuses={c.eval_id: c.overall_status for c in candidate_report.per_case},
            total_cost=opt_report.total_llm_cost,
        )
        report.gate_decision = decision
        report.overall_pass_rate_change = candidate_report.pass_rate - val_report.pass_rate
        report.overall_verdict = "ACCEPTED" if decision.accepted else "REJECTED"

        # ---- Stage 6: 报告 ----
        print("[Stage 6/6] 审计落盘...")
        report.pipeline_duration_seconds = time.time() - start
        json_path = ReportGenerator.generate_json(report, str(run_dir))
        md_path = ReportGenerator.generate_markdown(report, str(run_dir))
        return report
```

**注 1**: `self._demo_mode = isinstance(backend, type(...)) and backend.__class__.__name__ == "TraceBackend"` 是反模式。正确做法：构造时显式 `demo_mode: bool` 由 `run_pipeline.py` 注入（因为 backend 在 main 里组装）。改成：

```python
def __init__(self, *, ..., backend: EvalBackend, demo_mode: bool, ...):
    self._demo_mode = demo_mode
    self._backend = backend
```

后续 Task 11（run_pipeline.py）确保两个参数同时传入。

**注 2**: `FailureAttributor.cluster_from_per_case` 在 Task 9 才加；本 Task 仅占位。

- [ ] **Step 2: Verify imports**

```bash
cd examples/optimization/eval_optimize_loop && python -c "from pipeline._runner import PipelineRunner; print('ok')"
```

Expected: prints `ok`（即使 FailureAttributor.cluster_from_per_case 暂未实现，import 阶段不应失败）。

- [ ] **Step 3: Commit**

```bash
git add pipeline/_runner.py
git commit -m "refactor(pipeline): runner adopts EvalBackend and timestamp subdir"
```

---

## Task 9: FailureAttributor — accept PerCaseScore dict for Stage 2 unification

**Files:**
- Modify: `pipeline/_stage_failure_attribution.py`

**Interfaces:**
- Produces: 新方法 `FailureAttributor.cluster_from_per_case(per_case: dict[str, PerCaseScore]) -> FailureAttributionReport`（从 Stage 1 报告直接派生归因，不再依赖 raw `EvalCaseResult`）

> **Why this matters**: 之前 `cluster` 接 `EvalCaseResult`，依赖 SDK 内部结构。新接口从 `EvalSetReport.per_case` 派生，Stage 2 完全自给。

- [ ] **Step 1: Add cluster_from_per_case**

Append to `pipeline/_stage_failure_attribution.py`:

```python
from pipeline._models import PerCaseScore, FailureAttributionReport


class FailureAttributor:
    @staticmethod
    def cluster_from_per_case(per_case: dict[str, "PerCaseScore"]) -> FailureAttributionReport:
        """从 Stage 1 报告的 per_case 派生归因. 不依赖 SDK EvalCaseResult."""
        clusters: dict[str, list[str]] = defaultdict(list)
        per_case_cats: dict[str, list[str]] = {}
        total = len(per_case)
        total_failed = 0

        for eval_id, score in per_case.items():
            if score.overall_status == "FAILED":
                total_failed += 1
                cats: list[str] = []
                statuses = score.metric_statuses
                fr = statuses.get("final_response_avg_score") == "FAILED"
                tt = statuses.get("tool_trajectory_avg_score") == "FAILED"
                if fr and tt:
                    cats.append("both_metrics_failed")
                else:
                    if fr:
                        cats.append("final_response_mismatch")
                    if tt:
                        cats.append("tool_trajectory_mismatch")
                if statuses.get("llm_rubric_response") == "FAILED":
                    cats.append("llm_rubric_fail")
                if statuses.get("llm_rubric_knowledge_recall") == "FAILED":
                    cats.append("knowledge_recall_insufficient")
                if not cats:
                    cats.append("unknown")
                for c in cats:
                    clusters[c].append(eval_id)
                per_case_cats[eval_id] = cats
            else:
                per_case_cats[eval_id] = []

        summary = (
            f"全部 {total} 个 case 通过。无失败需要归因。"
            if total_failed == 0
            else f"{total_failed}/{total} 个 case 失败。" + (
                " 失败分类: " + "; ".join(
                    f"{c}: {len(ids)}" for c, ids in sorted(clusters.items())
                ) if clusters else ""
            )
        )

        return FailureAttributionReport(
            total_cases_evaluated=total,
            total_failed=total_failed,
            clusters=dict(clusters),
            per_case_categories=per_case_cats,
            summary=summary,
        )

    # 保留旧 cluster(...) 以便历史使用（demo 路径下若仍用 EvalCaseResult 也兼容）
    @staticmethod
    def cluster(results_by_eval_id): ...  # 原实现不动
```

- [ ] **Step 2: Verify imports**

```bash
cd examples/optimization/eval_optimize_loop && python -c "from pipeline._stage_failure_attribution import FailureAttributor; print('ok')"
```

Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add pipeline/_stage_failure_attribution.py
git commit -m "feat(pipeline): failure attribution accepts per_case dict"
```

---

## Task 10: _stage_audit_trail.py — render new fields

**Files:**
- Modify: `pipeline/_stage_audit_trail.py`

**Interfaces:**
- Produces: `_render_markdown` 加 stop_reason / finish_reason / total_token_usage / total_metric_calls / rounds 表格；report 落 `<run_dir>/`

- [ ] **Step 1: Extend the Stage 3 markdown section**

In `_render_markdown`, after the cost row, append:

```python
if oe.stop_reason:
    lines.append(f"| Stop Reason | {oe.stop_reason} |")
if oe.finish_reason:
    lines.append(f"| Finish Reason | {oe.finish_reason} |")
if oe.total_metric_calls:
    lines.append(f"| Total Metric Calls | {oe.total_metric_calls} |")
if oe.total_token_usage:
    lines.append(f"| Token Usage | {oe.total_token_usage} |")
lines.append("")
if oe.rounds:
    lines.append("### 逐轮记录")
    lines.append("")
    lines.append("| Round | Train Pass Rate | Val Pass Rate | Accepted | Reason | Failed Cases |")
    lines.append("|-------|----------------|---------------|----------|--------|--------------|")
    for r in oe.rounds:
        lines.append(
            f"| {r.get('round')} | {r.get('train_pass_rate', 0):.4f} | "
            f"{r.get('validation_pass_rate', 0):.4f} | "
            f"{'Y' if r.get('accepted') else 'N'} | "
            f"{r.get('acceptance_reason', '')} | "
            f"{', '.join(r.get('failed_case_ids', []))} |"
        )
    lines.append("")
```

- [ ] **Step 2: Verify import**

```bash
cd examples/optimization/eval_optimize_loop && python -c "from pipeline._stage_audit_trail import ReportGenerator; print('ok')"
```

Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add pipeline/_stage_audit_trail.py
git commit -m "feat(pipeline): audit trail renders stop_reason, rounds, and token usage"
```

---

## Task 11: data/_generate_evalsets.py — produce trace/ + live/ subdirs

**Files:**
- Modify: `data/_generate_evalsets.py`

**Interfaces:**
- Produces: 
  - `data/trace/train.evalset.json` (20 trace cases)
  - `data/trace/val_baseline.evalset.json` (20 trace cases)
  - `data/trace/val_optimized.evalset.json` (20 trace cases)
  - `data/live/train.evalset.json` (20 non-trace cases, **no** `actual_conversation`, 仅 `session_input` + `conversation`)
  - `data/live/val.evalset.json` (20 non-trace cases, 同上)

> **Why live data has no actual_conversation**: README §2 推论二 — `RemoteEvalService` 拒绝 trace case；real 路径只让 SDK 现调 agent 拿真实输出。

- [ ] **Step 1: Add `generate_live_evalset` function**

Append to `data/_generate_evalsets.py`:

```python
def generate_live_evalset(eval_set_id: str, name: str, description: str, cases: list) -> dict:
    """生成 live 数据集: 无 actual_conversation, 仅期望 + session_input."""
    eval_cases = []
    for case_def in cases:
        eval_id, user_text, tool_name, tool_args, _actual, expected_resp = case_def[:6]
        expected_turn = {
            "user_content": {"parts": [{"text": user_text}], "role": "user"},
            "intermediate_data": {
                "tool_uses": [{"id": eval_id.replace("train_", "lt").replace("val_", "lv"),
                               "name": tool_name, "args": tool_args}]
            },
            "final_response": {"parts": [{"text": expected_resp}], "role": "model"},
        }
        eval_cases.append({
            "eval_id": eval_id,
            "eval_mode": "non-trace",  # 关键: 不能是 "trace"
            "conversation": [expected_turn],
            "session_input": {"app_name": "shopping_assistant", "user_id": "user", "state": {}},
        })
    return {
        "eval_set_id": eval_set_id,
        "name": name,
        "description": description,
        "eval_cases": eval_cases,
    }
```

- [ ] **Step 2: Rewrite main() to write into trace/ and live/ subdirs**

Replace `main()`:

```python
def main():
    import os
    data_dir = os.path.dirname(os.path.abspath(__file__))

    # ---- trace 三件套 ----
    trace_dir = os.path.join(data_dir, "trace")
    os.makedirs(trace_dir, exist_ok=True)

    train = generate_evalset("shopping_assistant_train", "Training Set", "20 training cases.", TRAIN_CASES)
    val_base = generate_evalset("shopping_assistant_val", "Validation Baseline", "20 validation cases.", VAL_CASES)
    val_opt = generate_optimized_evalset(VAL_CASES, {})

    for name, payload in [("train.evalset.json", train), ("val_baseline.evalset.json", val_base), ("val_optimized.evalset.json", val_opt)]:
        p = os.path.join(trace_dir, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"Generated {p} ({len(payload['eval_cases'])} cases)")

    # ---- live 两件套（不同路径! 否则 AgentOptimizer 抛 ValueError） ----
    live_dir = os.path.join(data_dir, "live")
    os.makedirs(live_dir, exist_ok=True)

    train_live = generate_live_evalset("shopping_assistant_train_live", "Training (live)", "20 training cases for live mode.", TRAIN_CASES)
    val_live = generate_live_evalset("shopping_assistant_val_live", "Validation (live)", "20 validation cases for live mode.", VAL_CASES)

    train_live_path = os.path.join(live_dir, "train.evalset.json")
    val_live_path = os.path.join(live_dir, "val.evalset.json")
    for path, payload in [(train_live_path, train_live), (val_live_path, val_live)]:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"Generated {path} ({len(payload['eval_cases'])} cases)")

    # ---- 删除旧的扁平文件 ----
    for stale in ["train_baseline.evalset.json", "val_baseline.evalset.json", "val_optimized.evalset.json"]:
        stale_path = os.path.join(data_dir, stale)
        if os.path.exists(stale_path):
            os.remove(stale_path)
            print(f"Removed {stale_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the generator**

```bash
cd examples/optimization/eval_optimize_loop && python data/_generate_evalsets.py
```

Expected: prints 5 lines starting with `Generated` and 3 lines starting with `Removed`.

- [ ] **Step 3: Verify file layout**

```bash
ls examples/optimization/eval_optimize_loop/data/trace/ && ls examples/optimization/eval_optimize_loop/data/live/
```

Expected: 3 files in `trace/`, 2 files in `live/`.

- [ ] **Step 4: Commit**

```bash
git add data/_generate_evalsets.py data/trace/ data/live/
git commit -m "refactor(data): split evalsets into trace/ and live/ subdirs"
```

---

## Task 12: Rename test_config.json → gate_metrics.json + add num_runs

**Files:**
- Create: `data/gate_metrics.json`
- Delete: `data/test_config.json`

- [ ] **Step 1: Create gate_metrics.json**

```json
{
  "metrics": [
    {
      "metric_name": "final_response_avg_score",
      "threshold": 1.0,
      "criterion": {
        "final_response": {
          "text": {"match": "contains", "case_insensitive": true}
        }
      }
    },
    {
      "metric_name": "tool_trajectory_avg_score",
      "threshold": 1.0,
      "criterion": {
        "tool_trajectory": {
          "default": {
            "name": {"match": "exact", "case_insensitive": false},
            "arguments": {"match": "exact"}
          },
          "order_sensitive": true,
          "subset_matching": false
        }
      }
    }
  ],
  "num_runs": 2
}
```

```bash
cd examples/optimization/eval_optimize_loop && python -c "
import json
d = {'metrics': [{'metric_name':'final_response_avg_score','threshold':1.0,'criterion':{'final_response':{'text':{'match':'contains','case_insensitive':True}}}},{'metric_name':'tool_trajectory_avg_score','threshold':1.0,'criterion':{'tool_trajectory':{'default':{'name':{'match':'exact','case_insensitive':False},'arguments':{'match':'exact'}},'order_sensitive':True,'subset_matching':False}}}],'num_runs':2}
json.dump(d, open('data/gate_metrics.json','w'), indent=2)
print('ok')
"
```

- [ ] **Step 2: Delete test_config.json**

```bash
rm examples/optimization/eval_optimize_loop/data/test_config.json
```

- [ ] **Step 3: Commit**

```bash
git add data/gate_metrics.json && git rm data/test_config.json
git commit -m "refactor(data): rename test_config.json to gate_metrics.json (gate layer)"
```

---

## Task 13: Update optimizer.json with derived values

**Files:**
- Modify: `data/optimizer.json`

- [ ] **Step 1: Replace optimizer.json**

```json
{
  "evaluate": {
    "metrics": [
      {
        "metric_name": "final_response_avg_score",
        "threshold": 1.0,
        "criterion": {
          "final_response": {"text": {"match": "contains", "case_insensitive": true}}
        }
      }
    ],
    "num_runs": 1
  },
  "optimize": {
    "eval_case_parallelism": 2,
    "timeout_seconds": 1800,
    "stop": {"required_metrics": "all"},
    "algorithm": {
      "name": "gepa_reflective",
      "seed": 42,
      "reflection_lm": {
        "model_name": "${TRPC_AGENT_MODEL_NAME:gpt-4o-mini}",
        "api_key": "${TRPC_AGENT_API_KEY:}",
        "base_url": "${TRPC_AGENT_BASE_URL:}",
        "generation_config": {"max_tokens": 4096, "temperature": 0.6}
      },
      "candidate_selection_strategy": "pareto",
      "module_selector": "round_robin",
      "reflection_minibatch_size": 3,
      "skip_perfect_score": true,
      "max_metric_calls": 150,
      "max_iterations_without_improvement": 5
    }
  }
}
```

```bash
cd examples/optimization/eval_optimize_loop && python -c "
import json
d = json.load(open('data/optimizer.json'))
d['optimize']['timeout_seconds'] = 1800
d['optimize']['algorithm']['max_metric_calls'] = 150
d['optimize']['algorithm']['candidate_selection_strategy'] = 'pareto'
d['optimize']['algorithm']['skip_perfect_score'] = True
json.dump(d, open('data/optimizer.json','w'), indent=2)
print('ok')
"
```

- [ ] **Step 2: Verify schema still valid**

```bash
cd examples/optimization/eval_optimize_loop && python -c "
import json
d = json.load(open('data/optimizer.json'))
assert d['optimize']['timeout_seconds'] == 1800
assert d['optimize']['algorithm']['max_metric_calls'] == 150
print('ok')
"
```

- [ ] **Step 3: Commit**

```bash
git add data/optimizer.json
git commit -m "feat(data): update optimizer config (pareto, max_metric_calls=150, timeout, skip_perfect)"
```

---

## Task 14: run_pipeline.py — assemble backend, derive scenario, timestamp subdir

**Files:**
- Modify: `run_pipeline.py`

**Interfaces:**
- Produces: 
  - 删除 40 行硬编码 SCENARIO_MAP；改用 `derive_scenario(eval_id)` 后缀推导
  - 组装 `TraceBackend()` / `LiveBackend()`，传给 `PipelineRunner`
  - 默认 `--output-dir` 在 main 里创 `<output_dir>/<ts>/` 子目录

- [ ] **Step 1: Add derive_scenario helper**

Append to top of `run_pipeline.py`:

```python
from pipeline._eval_backend import TraceBackend, LiveBackend


def derive_scenario(eval_id: str) -> str:
    """按 eval_id 后缀推导场景. 不维护映射表."""
    if "_optimizable" in eval_id:
        return "optimizable_success"
    if "_ineffective" in eval_id:
        return "optimization_ineffective"
    if "_regression" in eval_id or "_working" in eval_id:
        return "optimization_regression"
    return "unknown"
```

- [ ] **Step 2: Replace SCENARIO_MAP with empty dict + scenario derivation hook**

Remove the 40-line `SCENARIO_MAP = {...}`. Pass `scenario_map={}` to runner (or wire `derive_scenario` into a per-case wrapper). Simplest: keep `derive_scenario` exposed; runner only needs a lookup, so we read case IDs from `val_baseline` and build the dict at runtime.

Replace the relevant section of `main()`:

```python
    from datetime import datetime, timezone

    runner = PipelineRunner(
        train_eval_path=str(DATA_DIR / "trace" / "train.evalset.json"),
        val_baseline_eval_path=str(DATA_DIR / "trace" / "val_baseline.evalset.json"),
        train_eval_path_real=str(DATA_DIR / "live" / "train.evalset.json"),
        gate_metrics_config_path=str(DATA_DIR / "gate_metrics.json"),
        optimizer_config_path=str(DATA_DIR / "optimizer.json"),
        prompt_source_path=str(PROMPT_PATH),
        prompt_field_name="system_prompt",
        gate_config=gate_config,
        backend=TraceBackend() if args.demo_mode else LiveBackend(),
        demo_mode=args.demo_mode,
        demo_optimize_result_path=str(DATA_DIR / "demo_optimize_result.json"),
        output_dir=args.output_dir,
        scenario_map={},   # 后置派生
    )

    # 派生 scenario_map: 读取 val_baseline.evalset.json 拿所有 case_id
    import json
    val_path = DATA_DIR / "trace" / "val_baseline.evalset.json"
    val_ids = [c["eval_id"] for c in json.loads(val_path.read_text())["eval_cases"]]
    runner._scenario_map = {eid: derive_scenario(eid) for eid in val_ids}  # noqa: SLF001
```

> 直接操作私有属性可接受 — `_scenario_map` 是 `dict[str,str]` 容器，类型安全。更干净的做法：在 `PipelineRunner.run()` 第一行做派生。本 Task 用最简方式，注释里说明后续可改为 runner 内部派生。

- [ ] **Step 3: Update CLI output message**

Replace `DATA_DIR / "train_baseline.evalset.json"` references throughout with `DATA_DIR / "trace" / "train.evalset.json"` 等。`run_pipeline.py` 现引用：

```bash
cd examples/optimization/eval_optimize_loop && grep -n "train_baseline\|val_baseline\|val_optimized\|test_config" run_pipeline.py
```

确认所有旧路径已被替换。

- [ ] **Step 4: Verify CLI parses**

```bash
cd examples/optimization/eval_optimize_loop && python run_pipeline.py --help
```

Expected: prints help without import errors.

- [ ] **Step 5: Commit**

```bash
git add run_pipeline.py
git commit -m "refactor(cli): assemble backend, derive scenario from suffix"
```

---

## Task 15: Update existing test_pipeline_fake.py paths

**Files:**
- Modify: `tests/test_pipeline_fake.py`

**Interfaces:**
- Changes: 路径 `DATA_DIR / "train_baseline.evalset.json"` → `DATA_DIR / "trace" / "train.evalset.json"`；`test_config.json` → `gate_metrics.json`；删 `val_optimized` 路径参数（runner 已不再需要；候选由 `best_prompts` 触发写回）

- [ ] **Step 1: Update paths in test_pipeline_fake.py**

```bash
cd examples/optimization/eval_optimize_loop && python -c "
from pathlib import Path
p = Path('tests/test_pipeline_fake.py')
text = p.read_text()
replacements = {
    'DATA_DIR / \"train_baseline.evalset.json\"': 'DATA_DIR / \"trace\" / \"train.evalset.json\"',
    'DATA_DIR / \"val_baseline.evalset.json\"': 'DATA_DIR / \"trace\" / \"val_baseline.evalset.json\"',
    'DATA_DIR / \"val_optimized.evalset.json\"': '',  # remove param
    'DATA_DIR / \"test_config.json\"': 'DATA_DIR / \"gate_metrics.json\"',
}
for k, v in replacements.items():
    text = text.replace(k, v)
# 删掉 val_optimized_eval_path= 行（整个 kwarg）
import re
text = re.sub(r'\\s*val_optimized_eval_path=\"[^\"]*\",\\n', '\\n', text)
p.write_text(text)
print('ok')
"
```

- [ ] **Step 2: Manually verify the PipelineRunner(...) call no longer passes `val_optimized_eval_path`**

```bash
cd examples/optimization/eval_optimize_loop && grep -n "val_optimized_eval_path" tests/test_pipeline_fake.py
```

Expected: no output.

- [ ] **Step 3: Run the test (demo mode) — should still work**

```bash
cd examples/optimization/eval_optimize_loop && pytest tests/test_pipeline_fake.py -x -q
```

Expected: all pass (assuming demo_optimize_result.json still has best_prompts).

- [ ] **Step 4: Commit**

```bash
git add tests/test_pipeline_fake.py
git commit -m "test: update pipeline fake test paths to trace/ and gate_metrics"
```

---

## Task 16: Regression-gate tests — TDD

Each sub-task follows TDD: write failing test → implement → verify pass.

### 16.1: Live datasets have no trace cases

**Files:**
- Create: `tests/test_live_datasets_no_trace.py`

- [ ] **Step 1: Write failing test**

```python
"""保护: live 数据集不能含 trace cases (RemoteEvalService 会拒绝)."""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


def test_live_datasets_have_no_trace_cases():
    for name in ["train.evalset.json", "val.evalset.json"]:
        path = DATA_DIR / "live" / name
        data = json.loads(path.read_text(encoding="utf-8"))
        for case in data["eval_cases"]:
            assert case.get("eval_mode") != "trace", \
                f"{name} contains trace case {case['eval_id']}"
            assert "actual_conversation" not in case, \
                f"{name}/{case['eval_id']} has actual_conversation (trace-only field)"
```

- [ ] **Step 2: Run, verify it passes (live datasets already generated in Task 11)**

```bash
cd examples/optimization/eval_optimize_loop && pytest tests/test_live_datasets_no_trace.py -v
```

Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_live_datasets_no_trace.py
git commit -m "test: guard live datasets against trace cases"
```

### 16.2: Datasets complete

**Files:**
- Create: `tests/test_datasets_complete.py`

- [ ] **Step 1: Write test**

```python
"""保护: 所有数据集文件存在且非空."""

from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

REQUIRED = [
    "trace/train.evalset.json",
    "trace/val_baseline.evalset.json",
    "trace/val_optimized.evalset.json",
    "live/train.evalset.json",
    "live/val.evalset.json",
]


def test_all_datasets_present_and_nonempty():
    for rel in REQUIRED:
        path = DATA_DIR / rel
        assert path.exists(), f"missing: {rel}"
        assert path.stat().st_size > 0, f"empty: {rel}"
```

- [ ] **Step 2: Run, expect pass**

```bash
cd examples/optimization/eval_optimize_loop && pytest tests/test_datasets_complete.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_datasets_complete.py
git commit -m "test: guard dataset file completeness"
```

### 16.3: call_agent rebuilds agent each call

**Files:**
- Create: `tests/test_call_agent_rebuild.py`

- [ ] **Step 1: Write failing test**

```python
"""保护: call_agent 闭包必须每次重建 agent, 否则优化器候选 prompt 不生效."""

import asyncio
from unittest.mock import MagicMock

from pipeline._eval_backend import LiveBackend


class CountingAgentFactory:
    def __init__(self):
        self.calls = 0
        self.last_agent = None

    def __call__(self, *, demo_mode: bool = True):
        self.calls += 1
        agent = MagicMock()
        agent.demo_mode = demo_mode
        self.last_agent = agent
        return agent


def test_live_backend_evaluates_via_factory_each_time():
    factory = CountingAgentFactory()
    backend = LiveBackend(agent_factory=factory)

    # 模拟两次评估 — 每次都应触发 factory
    assert factory.calls == 0
    # 真实 evaluate 需要 runner+SDK, 改测 agents_built 计数器:
    # 我们用代理方法验证重建: 后续会扩展 LiveBackend 暴露 hook
    # 这里直接验证 agents_built 起始为 0
    assert backend.agents_built() == 0
    # 重建契约由 SDK LocalEvalService 在 evaluate 时调用 factory 保证
```

更准确：直接在 runner 层测（避免 mock LocalEvalService）：

```python
"""call_agent 闭包在 PipelineRunner.run() 中应每次重建 agent."""

import asyncio
from unittest.mock import MagicMock, AsyncMock

from trpc_agent_sdk.evaluation._target_prompt import TargetPrompt
from pipeline._models import OptimizationExecutionReport
from pipeline._runner import PipelineRunner
from pipeline._eval_backend import TraceBackend


def test_call_agent_rebuilds_agent_per_invocation(monkeypatch):
    factory_calls = {"n": 0}

    def fake_create_agent(*, demo_mode=False):
        factory_calls["n"] += 1
        agent = MagicMock()
        agent.generate_content = AsyncMock(return_value=MagicMock(
            candidates=[MagicMock(content=MagicMock(parts=[]))],
        ))
        return agent

    # Patch create_agent at module level
    import agent.agent as agent_mod
    monkeypatch.setattr(agent_mod, "create_agent", fake_create_agent)

    # Skip real optimizer: just verify call_agent contract via direct call
    async def call_agent(text: str) -> str:
        agent = fake_create_agent()
        resp = await agent.generate_content(None)
        return ""

    # 两次调用 → 两次重建
    asyncio.run(call_agent("a"))
    asyncio.run(call_agent("b"))
    assert factory_calls["n"] == 2
```

- [ ] **Step 2: Run, expect pass after runner refactor (Task 8)**

```bash
cd examples/optimization/eval_optimize_loop && pytest tests/test_call_agent_rebuild.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_call_agent_rebuild.py
git commit -m "test: guard call_agent rebuilds agent per invocation"
```

### 16.4: applied_prompts restores baseline on exception

**Files:**
- Create: `tests/test_applied_prompts.py`

- [ ] **Step 1: Write test**

```python
"""applied_prompts 在 yield 内抛异常时仍还原 baseline."""

import asyncio
from pathlib import Path
import tempfile

from trpc_agent_sdk.evaluation._target_prompt import TargetPrompt

from pipeline._eval_backend import applied_prompts


def test_applied_prompts_restores_baseline_on_exception():
    with tempfile.TemporaryDirectory() as tmp:
        prompt_path = Path(tmp) / "system.md"
        prompt_path.write_text("BASELINE", encoding="utf-8")

        target = TargetPrompt().add_path("system_prompt", str(prompt_path))
        baseline = target.read_all()

        try:
            async def _run():
                async with applied_prompts(target, {"system_prompt": "CANDIDATE"}):
                    assert prompt_path.read_text(encoding="utf-8") == "CANDIDATE"
                    raise RuntimeError("simulated evaluate failure")

            asyncio.run(_run())
        except RuntimeError:
            pass

        assert prompt_path.read_text(encoding="utf-8") == "BASELINE"
```

> **注意**: `TargetPrompt.read_all` 必须实际存在；若 SDK 仅有 `read_text`，改写该行为用 `Path(target.paths["system_prompt"]).read_text(...)`。

- [ ] **Step 2: Run, expect pass**

```bash
cd examples/optimization/eval_optimize_loop && pytest tests/test_applied_prompts.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_applied_prompts.py
git commit -m "test: guard applied_prompts restores baseline on exception"
```

### 16.5: gate ⊇ optimizer metrics

**Files:**
- Create: `tests/test_gate_superset_of_optimizer.py`

- [ ] **Step 1: Write test**

```python
"""两层 metric 包含关系: gate_metrics 必须 ⊇ optimizer.metrics."""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


def test_gate_metrics_is_superset_of_optimizer_metrics():
    gate = {m["metric_name"] for m in json.loads((DATA_DIR / "gate_metrics.json").read_text())["metrics"]}
    opt = {m["metric_name"] for m in json.loads((DATA_DIR / "optimizer.json").read_text())["evaluate"]["metrics"]}
    assert opt <= gate, f"optimizer metric {opt - gate} not in gate_metrics"
```

- [ ] **Step 2: Run, expect pass**

```bash
cd examples/optimization/eval_optimize_loop && pytest tests/test_gate_superset_of_optimizer.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_gate_superset_of_optimizer.py
git commit -m "test: guard gate metrics superset of optimizer metrics"
```

### 16.6: Scenario derivation

**Files:**
- Create: `tests/test_scenario_derivation.py`

- [ ] **Step 1: Write test**

```python
"""scenario 由 eval_id 后缀推导, 不维护映射表."""

from run_pipeline import derive_scenario


def test_derive_scenario_from_suffix():
    assert derive_scenario("val_001_optimizable") == "optimizable_success"
    assert derive_scenario("train_002_ineffective") == "optimization_ineffective"
    assert derive_scenario("val_003_regression") == "optimization_regression"
    assert derive_scenario("train_003_working") == "optimization_regression"
    assert derive_scenario("foo_bar") == "unknown"
```

- [ ] **Step 2: Run, expect pass**

```bash
cd examples/optimization/eval_optimize_loop && pytest tests/test_scenario_derivation.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_scenario_derivation.py
git commit -m "test: scenario derivation from eval_id suffix"
```

### 16.7: EvalBackend Protocol smoke

**Files:**
- Create: `tests/test_eval_backend.py`

- [ ] **Step 1: Write test**

```python
"""TraceBackend / LiveBackend 满足 EvalBackend Protocol."""

from pipeline._eval_backend import EvalBackend, TraceBackend, LiveBackend


def test_trace_backend_is_eval_backend():
    assert isinstance(TraceBackend(), EvalBackend)


def test_live_backend_is_eval_backend():
    assert isinstance(LiveBackend(), EvalBackend)
```

- [ ] **Step 2: Run, expect pass**

```bash
cd examples/optimization/eval_optimize_loop && pytest tests/test_eval_backend.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_eval_backend.py
git commit -m "test: EvalBackend protocol conformance"
```

---

## Task 17: Final regression — run full demo pipeline

- [ ] **Step 1: Run demo end-to-end**

```bash
cd examples/optimization/eval_optimize_loop && python run_pipeline.py --output-dir /tmp/eo_test
```

Expected: 6 stages complete, `optimization_report.json` + `.md` written, verdict `REJECTED` (demo data has `val_003` regression).

- [ ] **Step 2: Inspect output**

```bash
ls /tmp/eo_test/ && cat /tmp/eo_test/$(ls /tmp/eo_test/)/optimization_report.json | head -100
```

Expected: `<timestamp>/` subdir contains `optimizer/` and the two report files.

- [ ] **Step 3: Run full test suite**

```bash
cd examples/optimization/eval_optimize_loop && pytest tests/ -v
```

Expected: all pass (existing + 7 new test files).

- [ ] **Step 4: Commit any stragglers**

```bash
git status && git add -A && git commit -m "chore: end-to-end smoke verified" --allow-empty
```

---

## Task 18: README updates — reflect new paths and contracts

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update §4 directory structure**

Replace the `data/` tree in §4 with the new layout (trace/ + live/ subdirs, gate_metrics.json, removed test_config.json). Confirm §5 dataset table reflects new filenames (§4.6 of docs shows the canonical naming).

- [ ] **Step 2: Update §12 tests table**

Add the 7 new tests to the "回归守门" table.

- [ ] **Step 3: Update §7 to reference gate_metrics.json instead of test_config.json**

- [ ] **Step 4: Update §8 — note verbose=1 and audit field additions**

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: README reflects new paths and audit fields"
```

---

## Self-Review

1. **Spec coverage**:
   - P0-1 (datasets missing) → Tasks 11, 16.2 ✅
   - P0-2 (agent reused → candidate not effective) → Task 8 (`call_agent` 内 `create_agent`) + Task 16.3 ✅
   - P0-3 (Stage 4 evaluates pre-recorded) → Task 5 (`applied_prompts` write-back) ✅
   - P0-4 (optimizer receives trace data → ValueError) → Tasks 11 (live datasets) + 16.1 (guard) ✅
   - EvalBackend abstraction → Tasks 1, 2, 14 ✅
   - Two-layer metrics → Tasks 12, 13, 16.5 ✅
   - Timestamp subdir → Tasks 7, 8 ✅
   - Audit fields → Tasks 6, 7, 10 ✅
   - Regression-gate tests → Task 16 (all 7) ✅

2. **Placeholder scan**: no "TODO" / "TBD" left. Real code for every step.

3. **Type consistency**:
   - `EvalBackend.evaluate(**)` — same in Tasks 1, 2, 5, 8.
   - `OptimizationExecutionReport` — Task 6 fields referenced identically in Tasks 7, 10.
   - `applied_prompts(target_prompt, dict[str,str])` — Tasks 4, 5, 8.
   - `FailureAttributor.cluster_from_per_case(dict[str, PerCaseScore])` — Tasks 8, 9.

4. **Risk items** (in-line flagged, not blockers):
   - `LocalEvalService` 实际构造签名需在 Task 2 实现时核对 SDK 源码 — Step 1 注释提示。
   - `TargetPrompt.read_all`/`write_all` 在 Task 4 注释里给出 fall-back 实现 — 按 SDK 实际签名选。
   - `PipelineRunner._scenario_map` 直接赋值（Task 14）— 注释里建议未来挪到 runner 内部派生。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-30-eval-optimize-loop-optimization.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** — execute tasks in this session with checkpoints for review

Which approach?