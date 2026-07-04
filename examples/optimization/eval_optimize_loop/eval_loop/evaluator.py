"""Example-local evaluator adapter.

This adapter intentionally keeps the surface small: it drives a model callable,
uses the fake judge for deterministic scoring, and returns dataclass results.
It can later be replaced with SDK AgentEvaluator integration.
"""

from __future__ import annotations

from typing import Iterable

from .attribution import attribute_failure
from .fake_judge import FakeJudge
from .fake_model import FakeModel
from .schemas import CaseResult
from .schemas import EvalCase
from .schemas import EvalResult
from .trace import make_trace


class ExampleEvaluator:
    """Evaluate prompt text against cases with fake model/judge components."""

    def __init__(self, model: FakeModel, judge: FakeJudge, *, trace_enabled: bool = False) -> None:
        self.model = model
        self.judge = judge
        self.trace_enabled = trace_enabled

    def evaluate(self, *, prompt_id: str, prompt: str, cases: Iterable[EvalCase], split: str) -> EvalResult:
        case_results: list[CaseResult] = []
        for case in cases:
            output, model_trace, cost = self.model.generate(prompt_id, prompt, case)
            judged = self.judge.score(case, output)
            failure_category = None
            failure_reason = None
            evidence = None
            if not judged.passed:
                failure_category, failure_reason, evidence = attribute_failure(
                    judged.error_code or "unknown_failure",
                    judged.evidence or "",
                )
            trace = make_trace(
                self.trace_enabled,
                prompt_id=prompt_id,
                case_id=case.case_id,
                model_trace=model_trace,
                judge_trace=judged.trace or {},
            )
            case_results.append(
                CaseResult(
                    case_id=case.case_id,
                    split=case.split,
                    score=round(judged.score, 6),
                    passed=judged.passed,
                    output=output,
                    trace=trace,
                    failure_category=failure_category,
                    failure_reason=failure_reason,
                    evidence=evidence,
                    cost=cost,
                    hard_failed=(not judged.passed and judged.score <= 0.0),
                )
            )

        score = round(sum(case.score for case in case_results) / len(case_results), 6) if case_results else 0.0
        total_cost = round(sum(case.cost for case in case_results), 6)
        return EvalResult(
            prompt_id=prompt_id,
            split=split,
            score=score,
            passed=all(case.passed for case in case_results),
            cost=total_cost,
            cases=case_results,
        )
