# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Remote (black-box) eval service driven by async call_agent(query)->str."""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from typing import Any
from typing import AsyncGenerator
from typing import Awaitable
from typing import Callable
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.log import error as log_error
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from ._eval_callbacks import Callbacks
from ._eval_callbacks import CallbacksRunner
from ._eval_callbacks import EvalSetRunResult
from ._eval_case import EvalCase
from ._eval_case import EvalModeTrace
from ._eval_case import Invocation
from ._eval_metrics import EvalMetric
from ._eval_metrics import EvalStatus
from ._eval_result import EvalCaseResult
from ._eval_result import EvalMetricResult
from ._eval_result import EvalMetricResultDetails
from ._eval_result import EvalMetricResultPerInvocation
from ._eval_result import EvaluationResult
from ._eval_result import PerInvocationResult
from ._eval_service_base import BaseEvalService
from ._eval_service_base import EvaluateConfig
from ._eval_service_base import EvaluateRequest
from ._eval_service_base import InferenceRequest
from ._eval_service_base import InferenceResult
from ._eval_service_base import InferenceStatus
from ._eval_set_results_manager_base import EvalSetResultsManager
from ._eval_sets_manager_base import EvalSetsManager
from ._evaluator_registry import EVALUATOR_REGISTRY
from ._evaluator_registry import EvaluatorRegistry

CallAgent = Callable[[str], Awaitable[str]]
# Metrics that cannot run under RemoteEvalService (black-box / call_agent
# mode) because they need information this service does not capture:
#   - ``tool_trajectory_avg_score`` needs per-step tool call traces.
#   - ``llm_rubric_knowledge_recall`` reads tool responses from
#     ``Invocation.intermediate_data``; this service always emits
#     ``intermediate_data=None`` (see ``_perform_inference_single_eval_item``),
#     so the judge would silently see "No knowledge search results were
#     found." for every case.
REMOTE_EVAL_INCOMPATIBLE_METRICS: frozenset[str] = frozenset({
    "tool_trajectory_avg_score",
    "llm_rubric_knowledge_recall",
})
EVAL_SESSION_ID_PREFIX = "___remote_eval___session___"


def _get_session_id() -> str:
    return f"{EVAL_SESSION_ID_PREFIX}{str(uuid.uuid4())}"


class RemoteEvalService(BaseEvalService):
    """Eval service for remote/black-box agents via call_agent."""

    def __init__(
        self,
        call_agent: CallAgent,
        eval_sets_manager: EvalSetsManager,
        evaluator_registry: Optional[EvaluatorRegistry] = None,
        eval_set_results_manager: Optional[EvalSetResultsManager] = None,
        session_id_supplier: Callable[[], str] = _get_session_id,
        callbacks: Optional[Callbacks] = None,
    ):
        self._validate_call_agent_is_async(call_agent)
        self._call_agent = call_agent
        self._eval_sets_manager = eval_sets_manager
        self._evaluator_registry = evaluator_registry or EVALUATOR_REGISTRY
        self._eval_set_results_manager = eval_set_results_manager
        self._session_id_supplier = session_id_supplier
        self._callbacks_runner = CallbacksRunner(callbacks or Callbacks())

    @staticmethod
    def _validate_call_agent_is_async(call_agent: Any) -> None:
        if not callable(call_agent):
            raise ValueError("call_agent must be callable.")
        if not inspect.iscoroutinefunction(call_agent):
            raise ValueError("call_agent must be an async function: async def call_agent(query: str) -> str")

    @staticmethod
    def _user_content_to_str(content: Content) -> str:
        parts = getattr(content, "parts", []) or []
        chunks: list[str] = []
        for part in parts:
            text = getattr(part, "text", None)
            if isinstance(text, str):
                chunks.append(text)
        return "".join(chunks)

    @staticmethod
    def _reject_trace_cases(eval_cases: list[EvalCase]) -> None:
        trace_ids = [case.eval_id for case in eval_cases if case.eval_mode == EvalModeTrace]
        if trace_ids:
            raise ValueError(f"call_agent mode is incompatible with trace cases: {trace_ids}")

    @override
    async def perform_inference(
        self,
        inference_request: InferenceRequest,
    ) -> AsyncGenerator[InferenceResult, None]:
        eval_set = self._eval_sets_manager.get_eval_set(
            app_name=inference_request.app_name,
            eval_set_id=inference_request.eval_set_id,
        )
        if not eval_set:
            raise ValueError(f"Eval set with id {inference_request.eval_set_id} not found for app "
                             f"{inference_request.app_name}")

        eval_cases = eval_set.eval_cases
        if inference_request.eval_case_ids:
            eval_cases = [c for c in eval_cases if c.eval_id in inference_request.eval_case_ids]
        self._reject_trace_cases(eval_cases)

        run_ctx: dict[str, Any] = {}
        start_time = time.monotonic()
        inference_results_list: list[InferenceResult] = []
        set_error: Optional[Exception] = None
        await self._callbacks_runner.run_before_inference_set(inference_request, run_ctx)
        semaphore = asyncio.Semaphore(value=inference_request.inference_config.parallelism)

        async def run_one(eval_case: EvalCase) -> InferenceResult:
            case_ctx = run_ctx.copy()
            session_id = self._session_id_supplier()
            await self._callbacks_runner.run_before_inference_case(
                inference_request,
                eval_case.eval_id,
                session_id,
                case_ctx,
            )
            case_start = time.monotonic()
            async with semaphore:
                result = await self._perform_inference_single_eval_item(
                    app_name=inference_request.app_name,
                    eval_set_id=inference_request.eval_set_id,
                    eval_case=eval_case,
                    session_id=session_id,
                )
                await self._callbacks_runner.run_after_inference_case(
                    inference_request,
                    result,
                    None,
                    case_start,
                    case_ctx,
                )
            return result

        try:
            tasks = [run_one(eval_case) for eval_case in eval_cases]
            for coro in asyncio.as_completed(tasks):
                inference_result = await coro
                inference_results_list.append(inference_result)
                yield inference_result
        except Exception as e:
            set_error = e
            raise
        finally:
            await self._callbacks_runner.run_after_inference_set(
                inference_request,
                inference_results_list,
                set_error,
                start_time,
                run_ctx,
            )

    async def _perform_inference_single_eval_item(
        self,
        app_name: str,
        eval_set_id: str,
        eval_case: EvalCase,
        session_id: Optional[str] = None,
    ) -> InferenceResult:
        if session_id is None:
            session_id = self._session_id_supplier()
        inference_result = InferenceResult(
            app_name=app_name,
            eval_set_id=eval_set_id,
            eval_case_id=eval_case.eval_id,
            session_id=session_id,
        )
        try:
            if not eval_case.conversation:
                raise ValueError(f"inference eval case (eval_case_id={eval_case.eval_id}, session_id={session_id}): "
                                 "conversation is required in call_agent mode")
            inferences: list[Invocation] = []
            for source_invocation in eval_case.conversation:
                query = self._user_content_to_str(source_invocation.user_content)
                response_text = await self._call_agent(query)
                inferences.append(
                    Invocation(
                        invocation_id=source_invocation.invocation_id,
                        user_content=source_invocation.user_content,
                        final_response=Content(parts=[Part(text=response_text)]),
                        intermediate_data=None,
                        creation_timestamp=time.time(),
                    ))
            inference_result.inferences = inferences
            inference_result.status = InferenceStatus.SUCCESS
            return inference_result
        except Exception as ex:  # pylint: disable=broad-except
            log_error(
                "Inference failed for eval case `%s` with error %s.",
                eval_case.eval_id,
                ex,
                exc_info=True,
            )
            inference_result.status = InferenceStatus.FAILURE
            inference_result.error_message = str(ex)
            return inference_result

    def _validate_remote_metric_compat(self, evaluate_config: EvaluateConfig) -> None:
        incompatible = sorted({
            metric.metric_name
            for metric in evaluate_config.eval_metrics if metric.metric_name in REMOTE_EVAL_INCOMPATIBLE_METRICS
        })
        if incompatible:
            raise ValueError("call_agent mode does not support metrics: "
                             f"{incompatible}. Please remove them from EvalConfig.")

    @override
    async def evaluate(
        self,
        evaluate_request: EvaluateRequest,
    ) -> AsyncGenerator[EvalCaseResult, None]:
        self._validate_remote_metric_compat(evaluate_request.evaluate_config)
        run_ctx: dict[str, Any] = {}
        start_time = time.monotonic()
        eval_case_results_list: list[EvalCaseResult] = []
        set_error: Optional[Exception] = None
        ir0 = evaluate_request.inference_results[0] if evaluate_request.inference_results else None
        app_name = ir0.app_name if ir0 else ""
        eval_set_id = ir0.eval_set_id if ir0 else ""
        await self._callbacks_runner.run_before_evaluate_set(evaluate_request, run_ctx)
        semaphore = asyncio.Semaphore(value=evaluate_request.evaluate_config.parallelism)

        async def run_one_eval(inference_result: InferenceResult) -> tuple[InferenceResult, EvalCaseResult]:
            case_ctx = run_ctx.copy()
            await self._callbacks_runner.run_before_evaluate_case(
                evaluate_request,
                inference_result.eval_case_id,
                case_ctx,
            )
            case_start = time.monotonic()
            async with semaphore:
                inference_result, eval_case_result = await self._evaluate_single_inference_result(
                    inference_result=inference_result,
                    evaluate_config=evaluate_request.evaluate_config,
                )
                await self._callbacks_runner.run_after_evaluate_case(
                    evaluate_request,
                    inference_result,
                    eval_case_result,
                    None,
                    case_start,
                    case_ctx,
                )
            return (inference_result, eval_case_result)

        try:
            tasks = [run_one_eval(ir) for ir in evaluate_request.inference_results]
            for coro in asyncio.as_completed(tasks):
                _, eval_case_result = await coro
                eval_case_results_list.append(eval_case_result)
                yield eval_case_result
            if self._eval_set_results_manager and eval_case_results_list and app_name:
                sorted_results = sorted(eval_case_results_list, key=lambda r: (r.run_id or 0, r.eval_id))
                self._eval_set_results_manager.save_eval_set_result(
                    app_name=app_name,
                    eval_set_id=eval_set_id,
                    eval_case_results=sorted_results,
                )
        except Exception as e:
            set_error = e
            raise
        finally:
            await self._callbacks_runner.run_after_evaluate_set(
                evaluate_request,
                EvalSetRunResult(
                    app_name=app_name,
                    eval_set_id=eval_set_id,
                    eval_case_results=eval_case_results_list,
                ),
                set_error,
                start_time,
                run_ctx,
            )

    async def _evaluate_single_inference_result(
        self,
        inference_result: InferenceResult,
        evaluate_config: EvaluateConfig,
    ) -> tuple[InferenceResult, EvalCaseResult]:
        eval_case = self._eval_sets_manager.get_eval_case(
            app_name=inference_result.app_name,
            eval_set_id=inference_result.eval_set_id,
            eval_case_id=inference_result.eval_case_id,
        )
        if eval_case is None:
            raise ValueError(f"Eval case with id {inference_result.eval_case_id} not found for "
                             f"app {inference_result.app_name} and eval set {inference_result.eval_set_id}.")

        expected_invocations = self._build_expected_invocations_for_eval(eval_case)
        eval_metric_result_per_invocation: list[EvalMetricResultPerInvocation] = []
        overall_eval_metric_results: list[EvalMetricResult] = []

        if inference_result.inferences:
            for idx, actual in enumerate(inference_result.inferences):
                expected = None
                if expected_invocations and idx < len(expected_invocations):
                    expected = expected_invocations[idx]
                eval_metric_result_per_invocation.append(
                    EvalMetricResultPerInvocation(
                        actual_invocation=actual,
                        expected_invocation=expected,
                        eval_metric_results=[],
                    ))

        case_error_message: Optional[str] = inference_result.error_message
        if inference_result.status == InferenceStatus.FAILURE:
            for eval_metric in evaluate_config.eval_metrics:
                overall_eval_metric_results.append(
                    EvalMetricResult(
                        metric_name=eval_metric.metric_name,
                        threshold=eval_metric.threshold,
                        criterion=eval_metric.criterion,
                        score=None,
                        eval_status=EvalStatus.NOT_EVALUATED,
                    ))
                for invocation in eval_metric_result_per_invocation:
                    invocation.eval_metric_results.append(
                        EvalMetricResult(
                            metric_name=eval_metric.metric_name,
                            threshold=eval_metric.threshold,
                            criterion=eval_metric.criterion,
                            score=None,
                            eval_status=EvalStatus.NOT_EVALUATED,
                        ))
            return (
                inference_result,
                EvalCaseResult(
                    eval_set_id=inference_result.eval_set_id,
                    eval_id=inference_result.eval_case_id,
                    run_id=getattr(inference_result, "run_id", None),
                    final_eval_status=EvalStatus.NOT_EVALUATED,
                    error_message=case_error_message,
                    overall_eval_metric_results=overall_eval_metric_results,
                    eval_metric_result_per_invocation=eval_metric_result_per_invocation,
                    session_id=inference_result.session_id or "",
                    session_details=None,
                    user_id=None,
                ),
            )

        for eval_metric in evaluate_config.eval_metrics:
            try:
                evaluation_result = await self._evaluate_metric(
                    eval_metric=eval_metric,
                    actual_invocations=inference_result.inferences or [],
                    expected_invocations=expected_invocations,
                )
            except Exception as e:  # pylint: disable=broad-except
                if case_error_message is None:
                    case_error_message = str(e)
                log_error(
                    "Metric evaluation failed for metric `%s` for eval case id '%s' with following error `%s`",
                    eval_metric.metric_name,
                    inference_result.eval_case_id,
                    e,
                    exc_info=True,
                )
                evaluation_result = EvaluationResult(overall_eval_status=EvalStatus.NOT_EVALUATED)

            reasons = [pr.reason for pr in evaluation_result.per_invocation_results if pr.reason is not None]
            rubric_scores: list[Any] = []
            for pr in evaluation_result.per_invocation_results:
                if pr.rubric_scores:
                    rubric_scores.extend(pr.rubric_scores)
            overall_reason = ";".join(reasons) if reasons else None
            overall_rubric = rubric_scores if rubric_scores else None
            overall_eval_metric_results.append(
                EvalMetricResult(
                    score=evaluation_result.overall_score,
                    eval_status=evaluation_result.overall_eval_status,
                    metric_name=eval_metric.metric_name,
                    threshold=eval_metric.threshold,
                    criterion=eval_metric.criterion,
                    details=EvalMetricResultDetails(
                        reason=overall_reason,
                        score=evaluation_result.overall_score,
                        rubric_scores=overall_rubric,
                    ) if (overall_reason is not None or overall_rubric is not None) else None,
                ))

            for idx, invocation in enumerate(eval_metric_result_per_invocation):
                if idx < len(evaluation_result.per_invocation_results):
                    invocation_result = evaluation_result.per_invocation_results[idx]
                else:
                    invocation_result = PerInvocationResult(actual_invocation=invocation.actual_invocation)
                invocation.eval_metric_results.append(
                    EvalMetricResult(
                        score=invocation_result.score,
                        eval_status=invocation_result.eval_status,
                        metric_name=eval_metric.metric_name,
                        threshold=eval_metric.threshold,
                        criterion=eval_metric.criterion,
                        details=EvalMetricResultDetails(
                            reason=invocation_result.reason,
                            score=invocation_result.score,
                            rubric_scores=invocation_result.rubric_scores,
                        ) if
                        (invocation_result.reason is not None or invocation_result.rubric_scores is not None) else None,
                    ))

        eval_case_result = EvalCaseResult(
            eval_set_id=inference_result.eval_set_id,
            eval_id=inference_result.eval_case_id,
            run_id=getattr(inference_result, "run_id", None),
            final_eval_status=self._generate_final_eval_status(overall_eval_metric_results),
            error_message=case_error_message,
            overall_eval_metric_results=overall_eval_metric_results,
            eval_metric_result_per_invocation=eval_metric_result_per_invocation,
            session_id=inference_result.session_id or "",
            session_details=None,
            user_id=None,
        )
        return (inference_result, eval_case_result)

    async def _evaluate_metric(
        self,
        eval_metric: EvalMetric,
        actual_invocations: list[Invocation],
        expected_invocations: Optional[list[Invocation]],
    ) -> EvaluationResult:
        evaluator = self._evaluator_registry.get_evaluator(eval_metric)
        if inspect.iscoroutinefunction(evaluator.evaluate_invocations):
            return await evaluator.evaluate_invocations(
                actual_invocations=actual_invocations,
                expected_invocations=expected_invocations,
            )
        return evaluator.evaluate_invocations(
            actual_invocations=actual_invocations,
            expected_invocations=expected_invocations,
        )

    @staticmethod
    def _build_expected_invocations_for_eval(eval_case: EvalCase) -> Optional[list[Invocation]]:
        if eval_case.conversation:
            return list(eval_case.conversation)
        return None

    @staticmethod
    def _generate_final_eval_status(overall_eval_metric_results: list[EvalMetricResult]) -> EvalStatus:
        final_eval_status = EvalStatus.NOT_EVALUATED
        for result in overall_eval_metric_results:
            if result.eval_status == EvalStatus.PASSED:
                final_eval_status = EvalStatus.PASSED
            elif result.eval_status == EvalStatus.FAILED:
                return EvalStatus.FAILED
        return final_eval_status
