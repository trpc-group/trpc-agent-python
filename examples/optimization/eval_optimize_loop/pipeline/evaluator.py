from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Callable, Literal

from trpc_agent_sdk.evaluation import AgentEvaluator
from trpc_agent_sdk.evaluation._eval_config import EvalConfig
from trpc_agent_sdk.evaluation._eval_set import EvalSet

from .models import SplitReport
from .normalization import normalize_eval_results


async def evaluate_split(
    eval_set_path: Path,
    *,
    call_agent: Callable[[str], Awaitable[str]],
    eval_config: EvalConfig,
    split: Literal["train", "validation"],
    metric_weights: dict[str, float],
) -> SplitReport:
    eval_set = EvalSet.model_validate_json(eval_set_path.read_text(encoding="utf-8"))
    _, _, _, results_by_eval_id = await AgentEvaluator.evaluate_eval_set(
        eval_set, call_agent=call_agent, eval_config=eval_config, num_runs=eval_config.num_runs, print_detailed_results=False
    )
    expected_eval_ids = {case.eval_id for case in eval_set.eval_cases}
    actual_eval_ids = set(results_by_eval_id)
    if actual_eval_ids != expected_eval_ids:
        missing = expected_eval_ids - actual_eval_ids
        unexpected = actual_eval_ids - expected_eval_ids
        raise ValueError(
            f"evaluation error for {eval_set_path.name}: incomplete evaluator results; "
            f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
        )
    normalized = normalize_eval_results(results_by_eval_id, split=split, metric_weights=metric_weights)
    return SplitReport.from_cases([case for _, case in sorted(normalized.items())])
