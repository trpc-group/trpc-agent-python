# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""LLM-backed evaluators: delegate to LLMJudge; registry for pluggable judge protocols."""

from __future__ import annotations

from typing import Any
from typing import Callable
from typing import List
from typing import Optional
from typing_extensions import override

from ._eval_case import Invocation
from ._eval_metrics import EvalMetric
from ._eval_metrics import EvalStatus
from ._eval_metrics import PrebuiltMetrics
from ._eval_result import EvaluationResult
from ._eval_result import PerInvocationResult
from ._evaluator_base import Evaluator
from ._llm_criterion import LLMJudgeCriterion
from ._llm_criterion import ScoreResult
from ._llm_judge import InvocationsAggregator
from ._llm_judge import LLMJudge
from ._llm_judge import MessagesConstructor
from ._llm_judge import ResponseScorer
from ._llm_judge import SamplesAggregator

# Metric names that use LLM judge; registry applies to these.
LLM_METRIC_NAMES = frozenset({
    PrebuiltMetrics.LLM_FINAL_RESPONSE.value,
    PrebuiltMetrics.LLM_RUBRIC_RESPONSE.value,
    PrebuiltMetrics.LLM_RUBRIC_KNOWLEDGE_RECALL.value,
})

# Type aliases for plain functions that users register.
MessagesConstructorFn = Callable[[list[Invocation], Optional[list[Invocation]], LLMJudgeCriterion, str], str]
ResponseScorerFn = Callable[[str, str], ScoreResult]
SamplesAggregatorFn = Callable[[list[ScoreResult], float], ScoreResult]
InvocationsAggregatorFn = Callable[[list[PerInvocationResult], float], tuple[Optional[float], EvalStatus]]


class _MessagesConstructorAdapter:
    """Adapts a plain function to MessagesConstructor."""

    def __init__(self, fn: MessagesConstructorFn) -> None:
        self._fn = fn

    def format_user_message(self, actuals, expecteds, criterion, metric_name):
        return self._fn(actuals, expecteds, criterion, metric_name)


class _ResponseScorerAdapter:
    """Adapts a plain function to ResponseScorer."""

    def __init__(self, fn: ResponseScorerFn) -> None:
        self._fn = fn

    def parse_response(self, response_text, metric_name):
        return self._fn(response_text, metric_name)


class _SamplesAggregatorAdapter:
    """Adapts a plain function to SamplesAggregator."""

    def __init__(self, fn: SamplesAggregatorFn) -> None:
        self._fn = fn

    def aggregate_samples(self, samples, threshold):
        return self._fn(samples, threshold)


class _InvocationsAggregatorAdapter:
    """Adapts a plain function to InvocationsAggregator."""

    def __init__(self, fn: InvocationsAggregatorFn) -> None:
        self._fn = fn

    def aggregate_invocations(self, results, threshold):
        return self._fn(results, threshold)


def _validate_metric(metric_name: str) -> None:
    """Raise ValueError if metric_name is not an LLM metric."""
    if metric_name not in LLM_METRIC_NAMES:
        raise ValueError(f"metric_name must be one of {sorted(LLM_METRIC_NAMES)}, got {metric_name!r}")


class LLMEvaluatorRegistry:
    """Registry for pluggable LLM judge protocols (messages, scorer, samples, invocations, tools).
    Plain functions are wrapped into protocol adapters; evaluators inject them into LLMJudge."""

    def __init__(self) -> None:
        self._messages_constructor: dict[str, MessagesConstructor] = {}
        self._response_scorer: dict[str, ResponseScorer] = {}
        self._samples_aggregator: dict[str, SamplesAggregator] = {}
        self._invocations_aggregator: dict[str, InvocationsAggregator] = {}
        self._judge_tools: dict[str, List[Any]] = {}

    def register_messages_constructor(self, metric_name: str, fn: MessagesConstructorFn) -> None:
        _validate_metric(metric_name)
        self._messages_constructor[metric_name] = _MessagesConstructorAdapter(fn)

    def register_response_scorer(self, metric_name: str, fn: ResponseScorerFn) -> None:
        _validate_metric(metric_name)
        self._response_scorer[metric_name] = _ResponseScorerAdapter(fn)

    def register_samples_aggregator(self, metric_name: str, fn: SamplesAggregatorFn) -> None:
        _validate_metric(metric_name)
        self._samples_aggregator[metric_name] = _SamplesAggregatorAdapter(fn)

    def register_invocations_aggregator(self, metric_name: str, fn: InvocationsAggregatorFn) -> None:
        _validate_metric(metric_name)
        self._invocations_aggregator[metric_name] = _InvocationsAggregatorAdapter(fn)

    def register_judge_tools(self, metric_name: str, tools: List[Any]) -> None:
        """Register tools for the judge LlmAgent (e.g. BaseTool, BaseToolSet, or callables)."""
        _validate_metric(metric_name)
        self._judge_tools[metric_name] = list(tools)

    def get_judge_tools(self, metric_name: str) -> Optional[List[Any]]:
        """Return registered tools for the judge agent, or None if not set."""
        return self._judge_tools.get(metric_name)

    def get_messages_constructor(self, metric_name: str) -> Optional[MessagesConstructor]:
        return self._messages_constructor.get(metric_name)

    def get_response_scorer(self, metric_name: str) -> Optional[ResponseScorer]:
        return self._response_scorer.get(metric_name)

    def get_samples_aggregator(self, metric_name: str) -> Optional[SamplesAggregator]:
        return self._samples_aggregator.get(metric_name)

    def get_invocations_aggregator(self, metric_name: str) -> Optional[InvocationsAggregator]:
        return self._invocations_aggregator.get(metric_name)

    def unregister_messages_constructor(self, metric_name: str) -> None:
        self._messages_constructor.pop(metric_name, None)

    def unregister_response_scorer(self, metric_name: str) -> None:
        self._response_scorer.pop(metric_name, None)

    def unregister_samples_aggregator(self, metric_name: str) -> None:
        self._samples_aggregator.pop(metric_name, None)

    def unregister_invocations_aggregator(self, metric_name: str) -> None:
        self._invocations_aggregator.pop(metric_name, None)

    def unregister_judge_tools(self, metric_name: str) -> None:
        self._judge_tools.pop(metric_name, None)


LLM_EVALUATOR_REGISTRY = LLMEvaluatorRegistry()


def _judge_for_metric(eval_metric: EvalMetric) -> LLMJudge:
    """Build LLMJudge for the metric, injecting registered protocols.
    Uses built-in default when a protocol is not registered."""
    name = eval_metric.metric_name
    return LLMJudge(
        eval_metric,
        messages_constructor=LLM_EVALUATOR_REGISTRY.get_messages_constructor(name),
        response_scorer=LLM_EVALUATOR_REGISTRY.get_response_scorer(name),
        samples_aggregator=LLM_EVALUATOR_REGISTRY.get_samples_aggregator(name),
        invocations_aggregator=LLM_EVALUATOR_REGISTRY.get_invocations_aggregator(name),
        judge_tools=LLM_EVALUATOR_REGISTRY.get_judge_tools(name),
    )


class LLMFinalResponseEvaluator(Evaluator):
    """LLM judge for final response (valid/invalid). Metric: llm_final_response."""

    def __init__(self, eval_metric: Optional[EvalMetric] = None) -> None:
        if not eval_metric:
            raise ValueError("eval_metric is required for LLMFinalResponseEvaluator")
        self._eval_metric = eval_metric
        self._judge = _judge_for_metric(eval_metric)

    @override
    async def evaluate_invocations(
        self,
        actual_invocations: list[Invocation],
        expected_invocations: Optional[list[Invocation]],
    ) -> EvaluationResult:
        return await self._judge.evaluate(actual_invocations, expected_invocations or [])


class LLMRubricResponseEvaluator(Evaluator):
    """LLM rubric-based response quality. Metric: llm_rubric_response."""

    def __init__(self, eval_metric: Optional[EvalMetric] = None) -> None:
        if not eval_metric:
            raise ValueError("eval_metric is required for LLMRubricResponseEvaluator")
        self._eval_metric = eval_metric
        self._judge = _judge_for_metric(eval_metric)

    @override
    async def evaluate_invocations(
        self,
        actual_invocations: list[Invocation],
        expected_invocations: Optional[list[Invocation]],
    ) -> EvaluationResult:
        return await self._judge.evaluate(actual_invocations, expected_invocations or [])


class LLMRubricKnowledgeRecallEvaluator(Evaluator):
    """LLM rubric knowledge recall. Metric: llm_rubric_knowledge_recall."""

    def __init__(self, eval_metric: Optional[EvalMetric] = None) -> None:
        if not eval_metric:
            raise ValueError("eval_metric is required for LLMRubricKnowledgeRecallEvaluator")
        self._eval_metric = eval_metric
        self._judge = _judge_for_metric(eval_metric)

    @override
    async def evaluate_invocations(
        self,
        actual_invocations: list[Invocation],
        expected_invocations: Optional[list[Invocation]],
    ) -> EvaluationResult:
        return await self._judge.evaluate(actual_invocations, expected_invocations or [])
