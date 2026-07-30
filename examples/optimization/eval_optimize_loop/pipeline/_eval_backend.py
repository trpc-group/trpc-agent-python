"""EvalBackend: 封装「agent 的实际输出从哪来」这一唯一差异。"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import AsyncIterator, Protocol, runtime_checkable

from trpc_agent_sdk.evaluation._agent_evaluator import AgentEvaluator
from trpc_agent_sdk.evaluation._eval_config import EvalConfig
from trpc_agent_sdk.evaluation._eval_metrics import EvalStatus
from trpc_agent_sdk.evaluation._eval_result import EvaluateResult
from trpc_agent_sdk.evaluation._target_prompt import TargetPrompt
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService

from agent.agent import create_agent
from pipeline._models import EvalSetReport, PerCaseScore


# App name used for the live runner. Eval cases store this in session_input.app_name
# when they want to override the default; otherwise the runner's app_name is used.
LIVE_RUNNER_APP_NAME = "shopping_assistant"


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


class LiveBackend:
    """real 模式 backend: 每次 evaluate() 现建 Runner（→ 新 agent → 重读 system.md）。

    Why a factory, not an instance: ``LlmAgent`` freezes ``instruction`` at
    construction time. Holding a long-lived agent would let Stage 4 pick up the
    *old* prompt. ``optimization.md`` §4.1-§4.3 require a fresh agent per
    evaluation, so the new ``system.md`` (written by the optimizer) is read on
    every call.
    """

    def __init__(self, agent_factory=create_agent) -> None:
        self._agent_factory = agent_factory
        self._agents_built: int = 0  # 供 Task 16.3 回归测试断言

    def agents_built(self) -> int:
        return self._agents_built

    async def evaluate(
        self,
        *,
        eval_set_path: str,
        metrics_config_path: str,
        num_runs: int = 1,
    ) -> tuple[EvaluateResult, EvalSetReport]:
        # 1. 现建 agent — 触发 _read_system_prompt() 重新读 system.md
        agent = self._agent_factory(demo_mode=False)
        self._agents_built += 1

        # 2. 包成 Runner 注入到 AgentEvaluator.get_executer(..., runner=...)
        #    SDK 签名: get_executer(..., runner: Optional[Runner] = None) 见
        #    _agent_evaluator.py:373
        runner = Runner(
            app_name=LIVE_RUNNER_APP_NAME,
            agent=agent,
            session_service=InMemorySessionService(),
        )

        executer = AgentEvaluator.get_executer(
            eval_dataset_file_path_or_dir=eval_set_path,
            eval_metrics_file_path_or_dir=metrics_config_path,
            num_runs=num_runs,
            runner=runner,
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


@asynccontextmanager
async def applied_prompts(
    target_prompt: TargetPrompt,
    prompts: dict[str, str],
) -> AsyncIterator[None]:
    """写入候选 prompt, 退出时还原 baseline. 异常路径也保证还原.

    Why this exists: ``AgentOptimizer.optimize(update_source=False)`` 在 ``finally``
    把源文件回滚成 baseline (optimization.md §3.3 FAQ)。Stage 4 若不显式写回候选,
    评的就是 baseline 而非候选。

    选型理由: 使用 SDK 主 API ``read_all`` / ``write_all``
    (``trpc_agent_sdk/evaluation/_target_prompt.py:128`` / ``:135``) —— 两者均为
    async, 覆盖全部已注册字段 (path-backed 与 callback-backed), 且 ``write_all``
    自带 tmp + os.replace 原子写与多字段部分失败回滚。不使用单字段
    ``read`` 逐个读写, 因为那样无法获得跨字段的原子性。
    """
    baseline = await target_prompt.read_all()
    try:
        await target_prompt.write_all(prompts)
        yield
    finally:
        await target_prompt.write_all(baseline)


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
