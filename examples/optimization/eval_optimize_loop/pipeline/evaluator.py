from __future__ import annotations

import json
from pathlib import Path
from typing import Awaitable, Callable, Literal

from trpc_agent_sdk.evaluation import AgentEvaluator
from trpc_agent_sdk.evaluation._eval_config import EvalConfig
from trpc_agent_sdk.evaluation._eval_set import EvalSet

from ..fake.fake_judge import register_fake_rubric_evaluator
from .models import SplitReport
from .normalization import normalize_eval_results


async def evaluate_split(
    eval_set_path: Path, *, call_agent: Callable[[str], Awaitable[str]], split: Literal["train", "validation"], metric_weights: dict[str, float]
) -> SplitReport:
    register_fake_rubric_evaluator()
    eval_set = EvalSet.model_validate_json(eval_set_path.read_text(encoding="utf-8"))
    config = EvalConfig.model_validate(json.loads((eval_set_path.parent / "optimizer.json").read_text(encoding="utf-8"))["evaluate"])
    _, _, _, results_by_eval_id = await AgentEvaluator.evaluate_eval_set(
        eval_set, call_agent=call_agent, eval_config=config, num_runs=config.num_runs, print_detailed_results=False
    )
    return SplitReport.from_cases(list(normalize_eval_results(results_by_eval_id, split=split, metric_weights=metric_weights).values()))
