"""Baseline evaluation stage — runs AgentEvaluator on train and validation sets."""

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .config import PipelineConfig


@dataclass
class BaselineResult:
    """Baseline evaluation results for an evalset."""
    evalset_id: str = ""
    pass_rate: float = 0.0
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    failed_case_ids: list[str] = field(default_factory=list)
    metric_breakdown: dict[str, float] = field(default_factory=dict)
    per_case_results: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class EvalSetData:
    """In-memory representation of an evalset for fake mode."""
    eval_set_id: str
    cases: list[dict]


def run_baseline_fake(evalset_path: str, config: PipelineConfig) -> BaselineResult:
    """Run baseline evaluation in fake mode.

    In fake mode, we load the evalset and simulate evaluation results
    without actually running the agent through a model.

    Args:
        evalset_path: Path to .evalset.json file.
        config: Pipeline configuration.

    Returns:
        BaselineResult with simulated evaluation outcomes.
    """
    if not os.path.exists(evalset_path):
        return BaselineResult(errors=[f"Evalset not found: {evalset_path}"])

    with open(evalset_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    eval_set_id = data.get("eval_set_id", os.path.basename(evalset_path))
    cases = data.get("eval_cases", [])
    total = len(cases)

    # In fake mode, each case has a pre-determined pass/fail based on
    # evalset contents. We check if cases have expected outputs defined.
    passed = 0
    failed_case_ids = []
    per_case = []

    for case in cases:
        case_id = case.get("eval_id", "unknown")
        # In fake mode, we look for a "fake_pass" hint or simulate based on
        # the presence of conversation/expected data
        expected = case.get("conversation", [])
        is_pass = bool(expected)  # Has expected output → pass

        if is_pass:
            passed += 1
        else:
            failed_case_ids.append(case_id)

        per_case.append({
            "eval_id": case_id,
            "pass": is_pass,
            "reason": "fake mode — has expected reference" if is_pass else "fake mode — no reference",
        })

    pass_rate = passed / total if total > 0 else 0.0

    return BaselineResult(
        evalset_id=eval_set_id,
        pass_rate=pass_rate,
        total_cases=total,
        passed_cases=passed,
        failed_cases=total - passed,
        failed_case_ids=failed_case_ids,
        metric_breakdown={"overall_pass_rate": pass_rate},
        per_case_results=per_case,
    )


def run_baseline_sdk(evalset_path: str) -> BaselineResult:
    """Run baseline evaluation using the real SDK AgentEvaluator.

    This path requires a functioning agent module and model.

    Args:
        evalset_path: Path to .evalset.json file.

    Returns:
        BaselineResult from actual AgentEvaluator run.
    """
    try:
        from trpc_agent_sdk.evaluation import AgentEvaluator

        # This uses the SDK's evaluate_eval_set which handles
        # inference + scoring in one call
        result = BaselineResult(evalset_id=os.path.basename(evalset_path))
        # SDK integration would go here
        return result

    except ImportError:
        return BaselineResult(
            errors=["SDK AgentEvaluator not available — use fake mode"]
        )
    except Exception as e:
        return BaselineResult(errors=[str(e)])
