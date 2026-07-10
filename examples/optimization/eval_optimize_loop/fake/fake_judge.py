from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from typing import Optional

from trpc_agent_sdk.evaluation._eval_case import Invocation
from trpc_agent_sdk.evaluation._eval_metrics import EvalMetric, EvalStatus
from trpc_agent_sdk.evaluation._eval_result import EvaluationResult, PerInvocationResult
from trpc_agent_sdk.evaluation._evaluator_base import Evaluator
from trpc_agent_sdk.evaluation._evaluator_registry import EVALUATOR_REGISTRY


@dataclass(frozen=True)
class FakeRubricResult:
    score: float
    reason: str


def _response_text(invocation: Invocation) -> str:
    response = invocation.final_response
    if response is None or not response.parts:
        return ""
    return "".join(part.text or "" for part in response.parts)


def _parse_response(response: str) -> dict[str, object] | None:
    try:
        payload = json.loads(response)
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def evaluate_fake_response(response: str, expected_response: str) -> FakeRubricResult:
    """Score the local fake-response rubric without a model or external state."""
    actual = _parse_response(response)
    if actual is None:
        return FakeRubricResult(0.0, "invalid JSON response")

    expected = _parse_response(expected_response)
    if expected is None:
        raise ValueError("fake rubric expected response must be a JSON object")

    score = 0.25
    reasons: list[str] = []
    required_fields = ("route", "tool", "arguments", "answer")
    missing_fields = [field for field in required_fields if field not in actual]
    if missing_fields:
        return FakeRubricResult(0.25, "missing required fields: " + ", ".join(missing_fields))

    if actual.get("route") == expected.get("route") and actual.get("tool") == expected.get("tool"):
        score += 0.25
    else:
        reasons.append("route or tool does not match expected response")

    expected_arguments = expected.get("arguments")
    actual_arguments = actual.get("arguments")
    if isinstance(expected_arguments, dict) and isinstance(actual_arguments, dict) and all(
        actual_arguments.get(name) == value for name, value in expected_arguments.items()
    ):
        score += 0.25
    else:
        reasons.append("required tool arguments do not match expected response")

    unknown_knowledge = expected.get("route") == "knowledge_gap" and expected.get("tool") == "none"
    answer = actual.get("answer")
    explicit_refusal = isinstance(answer, str) and any(
        phrase in answer.lower() for phrase in ("不能猜测", "cannot guess", "do not guess", "don't guess")
    )
    if not unknown_knowledge or explicit_refusal:
        score += 0.25
    else:
        reasons.append("unknown knowledge must explicitly refuse to guess")

    return FakeRubricResult(score, "; ".join(reasons) if reasons else "all fake rubric checks passed")


def score_fake_response(response: str, expected_response: str) -> float:
    """Return the deterministic fake-rubric score for a response pair."""
    return evaluate_fake_response(response, expected_response).score


def fake_rubric_score(response: str) -> float:
    """Backward-compatible structural score for callers without a reference response."""
    payload = _parse_response(response)
    return 1.0 if payload and all(key in payload for key in ("route", "tool", "arguments", "answer")) else 0.0


class FakeRubricEvaluator(Evaluator):
    """A local deterministic evaluator registered only by the fake example."""

    requires_reference = True

    def __init__(self, threshold: Optional[float] = None, eval_metric: Optional[EvalMetric] = None) -> None:
        if threshold is not None and eval_metric is not None:
            raise ValueError("Either eval_metric or threshold may be specified, not both")
        self._threshold = eval_metric.threshold if eval_metric is not None else threshold
        if self._threshold is None:
            self._threshold = 0.75

    def evaluate_invocations(
        self,
        actual_invocations: list[Invocation],
        expected_invocations: Optional[list[Invocation]],
    ) -> EvaluationResult:
        if expected_invocations is None:
            raise ValueError("expected_invocations is required for fake_rubric_score")
        if len(actual_invocations) != len(expected_invocations):
            raise ValueError("actual and expected invocations must contain the same number of invocations")

        per_invocation_results: list[PerInvocationResult] = []
        for actual, expected in zip(actual_invocations, expected_invocations):
            result = evaluate_fake_response(_response_text(actual), _response_text(expected))
            per_invocation_results.append(
                PerInvocationResult(
                    actual_invocation=actual,
                    expected_invocation=expected,
                    score=result.score,
                    eval_status=EvalStatus.PASSED if result.score >= self._threshold else EvalStatus.FAILED,
                    reason=result.reason,
                )
            )
        if not per_invocation_results:
            return EvaluationResult()

        overall_score = statistics.mean(result.score for result in per_invocation_results)
        return EvaluationResult(
            overall_score=overall_score,
            overall_eval_status=EvalStatus.PASSED if overall_score >= self._threshold else EvalStatus.FAILED,
            per_invocation_results=per_invocation_results,
        )


def register_fake_rubric_evaluator() -> None:
    """Register the example-only evaluator with the SDK's existing registry."""
    EVALUATOR_REGISTRY.register("fake_rubric_score", FakeRubricEvaluator)
