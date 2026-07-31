"""Deterministic replacements for LLM judge metrics in offline modes."""

from __future__ import annotations

import json
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar, Optional

from trpc_agent_sdk.evaluation import (
    EvalConfig,
    EvalMetric,
    EvalStatus,
    EvaluationResult,
    Evaluator,
    EvaluatorRegistry,
    FinalResponseEvaluator,
    Invocation,
    LLM_METRIC_NAMES,
    PerInvocationResult,
    RubricScore,
    get_all_tool_responses,
    get_llm_criterion_from_metric,
)

_RESPONSE_RULES = frozenset({
    "OFFLINE_RESPONSE_CONTAINS",
    "OFFLINE_RESPONSE_EQUALS",
    "OFFLINE_RESPONSE_EXACT_REFERENCE",
    "OFFLINE_RESPONSE_NON_EMPTY",
})
_KNOWLEDGE_RULES = frozenset({
    "OFFLINE_KNOWLEDGE_CONTAINS",
    "OFFLINE_KNOWLEDGE_NON_EMPTY",
})


@dataclass(frozen=True)
class _OfflineRubric:
    id: str
    rule: str
    operand: str


def _response_text(invocation: Optional[Invocation]) -> str:
    response = invocation.final_response if invocation is not None else None
    if response is None:
        return ""
    return "".join(part.text or "" for part in response.parts or [] if getattr(part, "text", None) is not None)


def _parse_rubrics(eval_metric: EvalMetric, supported: frozenset[str]) -> tuple[_OfflineRubric, ...]:
    criterion = get_llm_criterion_from_metric(eval_metric)
    if criterion is None or not criterion.rubrics:
        raise ValueError(f"{eval_metric.metric_name} requires criterion.llmJudge.rubrics")
    parsed = []
    ids = set()
    for rubric in criterion.rubrics:
        rubric_id = rubric.id
        if not rubric_id or rubric_id != rubric_id.strip() or rubric_id in ids:
            raise ValueError("offline rubric ids must be non-empty, trimmed, and unique")
        ids.add(rubric_id)
        rule = rubric.type.strip().upper()
        if rule not in supported:
            allowed = ", ".join(sorted(supported))
            raise ValueError(f"offline rubric {rubric_id!r} requires an explicit type; "
                             f"supported types: {allowed}")
        content = rubric.content
        operand = content.text if content is not None else ""
        if rule.endswith("_EQUALS") and content is None:
            raise ValueError(f"offline rubric {rubric_id!r} requires content.text")
        if rule.endswith("_CONTAINS") and not operand:
            raise ValueError(f"offline rubric {rubric_id!r} requires non-empty content.text")
        parsed.append(_OfflineRubric(id=rubric_id, rule=rule, operand=operand))
    return tuple(parsed)


def _evidence_fragments(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value, ) if value.strip() else ()
    if isinstance(value, Mapping):
        return tuple(fragment for key in sorted(value, key=str) for fragment in _evidence_fragments(value[key]))
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(fragment for item in value for fragment in _evidence_fragments(item))
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False), )


class _OfflineRubricEvaluator(Evaluator):
    requires_reference = False
    supported_rules: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, eval_metric: Optional[EvalMetric] = None) -> None:
        if eval_metric is None:
            raise ValueError("eval_metric is required for an offline rubric evaluator")
        criterion = get_llm_criterion_from_metric(eval_metric)
        if criterion is None:
            raise ValueError(f"{eval_metric.metric_name} requires criterion.llmJudge")
        self._threshold = eval_metric.threshold
        self._rubrics = _parse_rubrics(eval_metric, self.supported_rules)
        self._knowledge_tool_names = frozenset(criterion.get_knowledge_tool_names())

    def _score_rubric(
        self,
        rubric: _OfflineRubric,
        actual: Invocation,
        expected: Optional[Invocation],
    ) -> tuple[float, str]:
        raise NotImplementedError

    def evaluate_invocations(
        self,
        actual_invocations: list[Invocation],
        expected_invocations: Optional[list[Invocation]],
    ) -> EvaluationResult:
        per_invocation = []
        invocation_scores = []
        for index, actual in enumerate(actual_invocations):
            expected = (expected_invocations[index]
                        if expected_invocations is not None and index < len(expected_invocations) else None)
            results = [(rubric, *self._score_rubric(rubric, actual, expected)) for rubric in self._rubrics]
            score = statistics.fmean(result[1] for result in results)
            reason = "; ".join(result[2] for result in results)
            invocation_scores.append(score)
            per_invocation.append(
                PerInvocationResult(
                    actual_invocation=actual,
                    expected_invocation=expected,
                    score=score,
                    eval_status=(EvalStatus.PASSED if score >= self._threshold else EvalStatus.FAILED),
                    reason=reason,
                    rubric_scores=[
                        RubricScore(id=rubric.id, score=item_score, reason=item_reason)
                        for rubric, item_score, item_reason in results
                    ],
                ))
        if not per_invocation:
            return EvaluationResult(
                overall_score=0.0,
                overall_eval_status=EvalStatus.FAILED,
                per_invocation_results=[],
            )
        overall_score = statistics.fmean(invocation_scores)
        return EvaluationResult(
            overall_score=overall_score,
            overall_eval_status=(EvalStatus.PASSED if overall_score >= self._threshold else EvalStatus.FAILED),
            per_invocation_results=per_invocation,
        )


class _OfflineResponseEvaluator(_OfflineRubricEvaluator):
    supported_rules = _RESPONSE_RULES

    def _score_rubric(
        self,
        rubric: _OfflineRubric,
        actual: Invocation,
        expected: Optional[Invocation],
    ) -> tuple[float, str]:
        actual_text = _response_text(actual)
        if rubric.rule == "OFFLINE_RESPONSE_EXACT_REFERENCE":
            if expected is None or expected.final_response is None:
                raise ValueError(f"offline rubric {rubric.id!r} requires a reference final response")
            matched = actual_text == _response_text(expected)
        elif rubric.rule == "OFFLINE_RESPONSE_EQUALS":
            matched = actual_text == rubric.operand
        elif rubric.rule == "OFFLINE_RESPONSE_CONTAINS":
            matched = rubric.operand in actual_text
        else:
            matched = bool(actual_text.strip())
        return float(matched), f"{rubric.rule} {'matched' if matched else 'did not match'}"


class _OfflineKnowledgeEvaluator(_OfflineRubricEvaluator):
    supports_remote = False
    supported_rules = _KNOWLEDGE_RULES

    def _score_rubric(
        self,
        rubric: _OfflineRubric,
        actual: Invocation,
        expected: Optional[Invocation],
    ) -> tuple[float, str]:
        del expected
        fragments = tuple(fragment for response in get_all_tool_responses(actual.intermediate_data)
                          if response.name in self._knowledge_tool_names
                          for fragment in _evidence_fragments(response.response))
        matched = (rubric.operand in "\n".join(fragments)
                   if rubric.rule == "OFFLINE_KNOWLEDGE_CONTAINS" else bool(fragments))
        return float(matched), f"{rubric.rule} {'matched' if matched else 'did not match'}"


def prepare_offline_evaluation(eval_config: EvalConfig, ) -> tuple[EvalConfig, Optional[EvaluatorRegistry]]:
    """Build a run-local deterministic config and registry for offline modes."""

    configured = eval_config.get_eval_metrics()
    llm_names = {metric.metric_name for metric in configured if metric.metric_name in LLM_METRIC_NAMES}
    if not llm_names:
        return eval_config, None
    metrics = []
    for metric in configured:
        payload = metric.model_dump(mode="json", by_alias=True)
        if metric.metric_name == "llm_final_response":
            payload["criterion"] = {"finalResponse": {"text": {"match": "exact"}}}
        metrics.append(payload)
    payload = eval_config.model_dump(mode="python", by_alias=False)
    payload.update({"criteria": {}, "metrics": metrics})
    offline_config = EvalConfig.model_validate(payload)
    registry = EvaluatorRegistry()
    replacements = {
        "llm_final_response": FinalResponseEvaluator,
        "llm_rubric_response": _OfflineResponseEvaluator,
        "llm_rubric_knowledge_recall": _OfflineKnowledgeEvaluator,
    }
    for metric_name in llm_names:
        registry.register(metric_name, replacements[metric_name])
    for metric in offline_config.get_eval_metrics():
        if metric.metric_name in llm_names and metric.metric_name != "llm_final_response":
            registry.get_evaluator(metric)
    return offline_config, registry
