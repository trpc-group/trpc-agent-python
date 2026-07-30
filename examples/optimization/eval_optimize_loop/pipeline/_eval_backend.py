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
