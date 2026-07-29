# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared fixtures for the eval-optimize-loop tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
if str(_EXAMPLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_ROOT))

from pipeline import DeltaAnalyzer  # type: ignore  # noqa: E402
from pipeline.types import BaselineCaseRecord  # type: ignore  # noqa: E402
from pipeline.types import BaselineSplitResult  # type: ignore  # noqa: E402


@pytest.fixture
def example_root() -> Path:
    return _EXAMPLE_ROOT


def metric_result(
    metric_name: str,
    *,
    score: float = 0.0,
    threshold: float = 1.0,
    eval_status: str = "FAILED",
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "metric_name": metric_name,
        "score": score,
        "threshold": threshold,
        "eval_status": eval_status,
        "reason": reason,
    }


def case_record(
    case_id: str,
    *,
    passed: bool,
    metric_results: list[dict[str, Any]],
    expected_tool_calls: list[dict[str, Any]] | None = None,
    actual_tool_calls: list[dict[str, Any]] | None = None,
    expected_final: str = "expected",
    actual_final: str = "actual",
    failure_reason: str = "failed",
) -> BaselineCaseRecord:
    if metric_results:
        metric_score = sum(float(entry["score"]) for entry in metric_results) / len(metric_results)
    else:
        metric_score = 1.0 if passed else 0.0
    return BaselineCaseRecord(
        id=case_id,
        metric_score=metric_score,
        metric_scores={entry["metric_name"]: float(entry["score"])
                       for entry in metric_results},
        passed=passed,
        failure_reason="" if passed else failure_reason,
        trace={
            "user": "question",
            "expected": expected_final,
            "actual": actual_final,
            "tool_calls": actual_tool_calls or [],
        },
        latency=0.01,
        cost=0.0,
        evaluator_metadata={
            "error_message": None,
            "final_eval_status": "PASSED" if passed else "FAILED",
            "overall_metric_results": metric_results,
            "failed_metric_results": [entry for entry in metric_results if entry["eval_status"] != "PASSED"],
            "expected_tool_calls": expected_tool_calls or [],
            "actual_tool_calls": actual_tool_calls or [],
            "expected_final_response": expected_final,
            "actual_final_response": actual_final,
        },
    )


def delta_from_scores(
    *,
    train_pairs: list[tuple[str, bool, float, bool, float]],
    val_pairs: list[tuple[str, bool, float, bool, float]],
    baseline_cost: float = 0.0,
    candidate_cost: float = 0.0,
) -> Any:
    return DeltaAnalyzer().analyze(
        train_baseline=split_from_pairs("train_base", train_pairs, baseline=True, cost=baseline_cost),
        train_candidate=split_from_pairs("train_candidate", train_pairs, baseline=False, cost=candidate_cost),
        val_baseline=split_from_pairs("val_base", val_pairs, baseline=True, cost=baseline_cost),
        val_candidate=split_from_pairs("val_candidate", val_pairs, baseline=False, cost=candidate_cost),
    )


def split_from_pairs(
    eval_set_id: str,
    pairs: list[tuple[str, bool, float, bool, float]],
    *,
    baseline: bool,
    cost: float,
) -> BaselineSplitResult:
    records = []
    for case_id, baseline_passed, baseline_score, candidate_passed, candidate_score in pairs:
        passed = baseline_passed if baseline else candidate_passed
        score = baseline_score if baseline else candidate_score
        case = case_record(
            case_id,
            passed=passed,
            metric_results=[metric_result(
                "m",
                score=score,
                eval_status="PASSED" if passed else "FAILED",
            )],
        )
        case.cost = cost
        records.append(case)
    return BaselineSplitResult(eval_set_id=eval_set_id, cases=records)


def rule(decision: Any, rule_name: str) -> Any:
    return next(result for result in decision.rule_results if result.rule_name == rule_name)


def install_fake_evaluation_sdk(monkeypatch: Any, example_root: Path) -> dict[str, Any]:
    from pipeline.optimization import FINAL_ANSWER_FIX_MARKER  # type: ignore  # noqa: E402

    captured: dict[str, Any] = {}
    baseline_prompts = {
        "system_prompt": (example_root / "prompts" / "system.md").read_text(encoding="utf-8"),
        "skill": (example_root / "prompts" / "skill.md").read_text(encoding="utf-8"),
    }
    best_prompts = {
        "system_prompt": baseline_prompts["system_prompt"] + f"\n{FINAL_ANSWER_FIX_MARKER}\n",
        "skill": baseline_prompts["skill"],
    }

    async def fake_optimize(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(
            best_prompts=best_prompts,
            baseline_prompts=baseline_prompts,
            rounds=[
                SimpleNamespace(
                    round=1,
                    optimized_field_names=["system_prompt"],
                    candidate_prompts=best_prompts,
                    acceptance_reason="stub accepted final_answer_mismatch fix",
                    accepted=True,
                    validation_pass_rate=1.0,
                    duration_seconds=0.01,
                    round_llm_cost=0.0,
                )
            ],
            total_rounds=1,
            total_llm_cost=0.0,
            duration_seconds=0.01,
            status="SUCCEEDED",
            finish_reason="stubbed",
            pass_rate_improvement=1.0,
        )

    class FakeAgentOptimizer:

        optimize = staticmethod(fake_optimize)

    class FakeTargetPrompt:

        def __init__(self) -> None:
            self._names: list[str] = []

        def add_path(self, name: str, path: str) -> "FakeTargetPrompt":
            self._names.append(name)
            return self

        def names(self) -> list[str]:
            return self._names

    class FakeEvalConfig:

        @classmethod
        def model_validate(cls, payload: dict[str, Any]) -> dict[str, Any]:
            return payload

    class FakeEvalSet:

        def __init__(
            self,
            *,
            eval_set_id: str,
            app_name: str = "",
            name: str = "",
            description: str = "",
            eval_cases: list[Any] | None = None,
        ) -> None:
            self.eval_set_id = eval_set_id
            self.app_name = app_name
            self.name = name
            self.description = description
            self.eval_cases = eval_cases or []

        @classmethod
        def model_validate_json(cls, content: str) -> "FakeEvalSet":
            payload = json.loads(content)
            cases = [_fake_eval_case(case) for case in payload["eval_cases"]]
            return cls(
                eval_set_id=payload["eval_set_id"],
                app_name=payload.get("app_name", ""),
                name=payload.get("name", ""),
                description=payload.get("description", ""),
                eval_cases=cases,
            )

    class FakeAgentEvaluator:

        @staticmethod
        async def evaluate_eval_set(eval_set: FakeEvalSet,
                                    **kwargs: Any) -> tuple[None, list[str], list[str], dict[str, list[Any]]]:
            call_agent = kwargs.get("call_agent")
            results = {}
            for case in eval_set.eval_cases:
                user_text = content_text(case.conversation[0].user_content)
                expected_text = content_text(case.conversation[0].final_response)
                actual_text = await call_agent(user_text) if call_agent else content_text(
                    case.actual_conversation[0].final_response)
                passed = expected_text in actual_text
                metric = SimpleNamespace(
                    metric_name="final_response_avg_score",
                    score=1.0 if passed else 0.0,
                    threshold=1.0,
                    eval_status=SimpleNamespace(name="PASSED" if passed else "FAILED"),
                    details=SimpleNamespace(reason=None if passed else "Final response does not match."),
                )
                result = SimpleNamespace(
                    eval_id=case.eval_id,
                    final_eval_status=SimpleNamespace(name="PASSED" if passed else "FAILED"),
                    error_message=None,
                    overall_eval_metric_results=[metric],
                    eval_metric_result_per_invocation=[
                        SimpleNamespace(
                            expected_invocation=case.conversation[0],
                            actual_invocation=SimpleNamespace(
                                user_content=case.conversation[0].user_content,
                                final_response=fake_content(actual_text),
                                intermediate_data=None,
                            ),
                        )
                    ],
                )
                results[case.eval_id] = [result]
            return None, [], [], results

    monkeypatch.setitem(
        sys.modules,
        "trpc_agent_sdk.evaluation",
        SimpleNamespace(
            AgentEvaluator=FakeAgentEvaluator,
            AgentOptimizer=FakeAgentOptimizer,
            EvalConfig=FakeEvalConfig,
            EvalSet=FakeEvalSet,
            TargetPrompt=FakeTargetPrompt,
            get_all_tool_calls=lambda intermediate_data: [],
        ),
    )
    return captured


def _fake_eval_case(case: dict[str, Any]) -> Any:
    return SimpleNamespace(
        eval_id=case["eval_id"],
        conversation=[_fake_invocation(invocation) for invocation in case.get("conversation", [])],
        actual_conversation=[_fake_invocation(invocation) for invocation in case.get("actual_conversation", [])],
    )


def _fake_invocation(invocation: dict[str, Any]) -> Any:
    return SimpleNamespace(
        user_content=fake_content(_raw_json_content_text(invocation.get("user_content"))),
        final_response=fake_content(_raw_json_content_text(invocation.get("final_response"))),
        intermediate_data=None,
    )


def fake_content(text: str) -> Any:
    return SimpleNamespace(parts=[SimpleNamespace(text=text)])


def content_text(content: Any) -> str:
    return "\n".join(part.text or "" for part in content.parts if part.text).strip()


def _raw_json_content_text(content: dict[str, Any] | None) -> str:
    if not content:
        return ""
    return "\n".join(str(part.get("text") or "") for part in content.get("parts", []) if part.get("text")).strip()
