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
