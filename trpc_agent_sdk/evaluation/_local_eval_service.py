# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
#
# Below code are copy and modified from https://github.com/google/adk-python.git
#
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Local evaluation service implementation.

This module provides LocalEvalService for running agent evaluations locally.
"""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from typing import Any
from typing import AsyncGenerator
from typing import Callable
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk._runners import Runner
from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.artifacts import BaseArtifactService
from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.log import error as log_error
from trpc_agent_sdk.memory import BaseMemoryService
from trpc_agent_sdk.sessions import BaseSessionService
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content

from ._eval_callbacks import Callbacks
from ._eval_callbacks import CallbacksRunner
from ._eval_callbacks import EvalSetRunResult
from ._eval_case import EvalCase
from ._eval_case import EvalModeTrace
from ._eval_case import IntermediateData
from ._eval_case import Invocation
from ._eval_case import InvocationEvent
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
from ._eval_session_service import EvalSessionService
from ._eval_set_results_manager_base import EvalSetResultsManager
from ._eval_sets_manager_base import EvalSetsManager
from ._evaluator_registry import EVALUATOR_REGISTRY
from ._evaluator_registry import EvaluatorRegistry
from ._user_simulator_base import Status
from ._user_simulator_provider import UserSimulatorProvider

EVAL_SESSION_ID_PREFIX = "___eval___session___"
DEFAULT_EVAL_USER_ID = "test_user_id"


def _get_session_id() -> str:
    """Generate a unique session ID for evaluation."""
    return f"{EVAL_SESSION_ID_PREFIX}{str(uuid.uuid4())}"


class LocalEvalService(BaseEvalService):
    """Local implementation of evaluation service.

    This service runs evaluations locally using the TRPC agent framework.
    """

    def __init__(
        self,
        root_agent: BaseAgent,
        eval_sets_manager: EvalSetsManager,
        evaluator_registry: Optional[EvaluatorRegistry] = None,
        session_service: Optional[BaseSessionService] = None,
        artifact_service: Optional[BaseArtifactService] = None,
        memory_service: Optional[BaseMemoryService] = None,
        eval_set_results_manager: Optional[EvalSetResultsManager] = None,
        session_id_supplier: Callable[[], str] = _get_session_id,
        user_simulator_provider=None,
        runner: Optional[Runner] = None,
        callbacks: Optional[Callbacks] = None,
    ):
        """Initialize the local evaluation service.

        Args:
            root_agent: The agent to evaluate
            eval_sets_manager: Manager for eval sets storage
            evaluator_registry: Registry of metric evaluators
            session_service: Session service for maintaining state
            artifact_service: Artifact service (optional)
            memory_service: Memory service (optional)
            eval_set_results_manager: Manager for saving evaluation results
            session_id_supplier: Function to generate session IDs
            user_simulator_provider: Provider for user simulators
            runner: Optional user-provided Runner; when set, use it as-is and only
                update its session when session_input exists and values are set in case.
            callbacks: Optional lifecycle callbacks (before/after inference and evaluate).
        """
        self._root_agent = root_agent
        self._eval_sets_manager = eval_sets_manager
        self._evaluator_registry = evaluator_registry or EVALUATOR_REGISTRY
        self._session_service = session_service or InMemorySessionService()
        self._artifact_service = artifact_service
        self._memory_service = memory_service
        self._eval_set_results_manager = eval_set_results_manager
        self._session_id_supplier = session_id_supplier
        self._user_simulator_provider = user_simulator_provider or UserSimulatorProvider()
        self._runner = runner
        self._callbacks = callbacks
        self._callbacks_runner = CallbacksRunner(callbacks or Callbacks())

    @staticmethod
    def _get_user_id_from_eval_case(eval_case: EvalCase) -> tuple[str, bool]:
        """Get user_id from eval case session_input, or default. Returns (user_id, set_in_case)."""
        if eval_case.session_input and eval_case.session_input.user_id:
            return (eval_case.session_input.user_id, True)
        return (DEFAULT_EVAL_USER_ID, False)

    @staticmethod
    def _get_initial_state_from_eval_case(eval_case: EvalCase) -> tuple[dict[str, Any], bool]:
        """Get initial session state from eval case session_input. Returns (state, set_in_case)."""
        if eval_case.session_input:
            return (eval_case.session_input.state or {}, True)
        return ({}, False)

    @staticmethod
    def _get_session_app_name(eval_case: EvalCase, fallback_app_name: str) -> tuple[str, bool]:
        """App name for session storage. Returns (app_name, set_in_case)."""
        if eval_case.session_input and eval_case.session_input.app_name:
            return (eval_case.session_input.app_name, True)
        return (fallback_app_name, False)

    @override
    async def perform_inference(
        self,
        inference_request: InferenceRequest,
    ) -> AsyncGenerator[InferenceResult, None]:
        """Generate inferences for eval cases.

        Args:
            inference_request: The inference request

        Yields:
            InferenceResult for each eval case
        """
        # Get the eval set from storage
        eval_set = self._eval_sets_manager.get_eval_set(
            app_name=inference_request.app_name,
            eval_set_id=inference_request.eval_set_id,
        )

        if not eval_set:
            raise ValueError(f"Eval set with id {inference_request.eval_set_id} not found for app "
                             f"{inference_request.app_name}")

        # Select eval cases for inferencing
        eval_cases = eval_set.eval_cases
        if inference_request.eval_case_ids:
            eval_cases = [eval_case for eval_case in eval_cases if eval_case.eval_id in inference_request.eval_case_ids]

        run_ctx: dict[str, Any] = {}
        start_time = time.monotonic()
        inference_results_list: list[InferenceResult] = []
        set_error: Optional[Exception] = None

        await self._callbacks_runner.run_before_inference_set(inference_request, run_ctx)

        semaphore = asyncio.Semaphore(value=inference_request.inference_config.parallelism)

        async def run_one(eval_case: EvalCase) -> InferenceResult:
            case_ctx = run_ctx.copy()
            session_id = self._session_id_supplier()
            await self._callbacks_runner.run_before_inference_case(inference_request, eval_case.eval_id, session_id,
                                                                   case_ctx)
            case_start = time.monotonic()
            async with semaphore:
                result = await self._perform_inference_single_eval_item(
                    app_name=inference_request.app_name,
                    eval_set_id=inference_request.eval_set_id,
                    eval_case=eval_case,
                    root_agent=self._root_agent,
                    session_id=session_id,
                )
                await self._callbacks_runner.run_after_inference_case(inference_request, result, None, case_start,
                                                                      case_ctx)
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

    @override
    async def evaluate(
        self,
        evaluate_request: EvaluateRequest,
    ) -> AsyncGenerator[EvalCaseResult, None]:
        """Evaluate inference results.

        Args:
            evaluate_request: The evaluation request

        Yields:
            EvalCaseResult for each evaluated inference
        """
        run_ctx: dict[str, Any] = {}
        start_time = time.monotonic()
        eval_case_results_list: list[EvalCaseResult] = []
        set_error: Optional[Exception] = None
        ir0 = evaluate_request.inference_results[0] if evaluate_request.inference_results else None
        app_name = ir0.app_name if ir0 else ""
        eval_set_id = ir0.eval_set_id if ir0 else ""

        await self._callbacks_runner.run_before_evaluate_set(evaluate_request, run_ctx)

        semaphore = asyncio.Semaphore(value=evaluate_request.evaluate_config.parallelism)

        async def run_one_eval(inference_result: InferenceResult, ) -> tuple[InferenceResult, EvalCaseResult]:
            case_ctx = run_ctx.copy()
            await self._callbacks_runner.run_before_evaluate_case(evaluate_request, inference_result.eval_case_id,
                                                                  case_ctx)
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
                inference_result, eval_case_result = await coro
                eval_case_results_list.append(eval_case_result)
                yield eval_case_result
            if self._eval_set_results_manager and eval_case_results_list and app_name:
                sorted_results = sorted(
                    eval_case_results_list,
                    key=(lambda r: (r.run_id or 0, r.eval_id)),
                )
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
        """Evaluate a single inference result.

        Args:
            inference_result: The inference result to evaluate
            evaluate_config: Evaluation configuration

        Returns:
            Tuple of (InferenceResult, EvalCaseResult)
        """
        # Get expected invocations from the eval case
        eval_case = self._eval_sets_manager.get_eval_case(
            app_name=inference_result.app_name,
            eval_set_id=inference_result.eval_set_id,
            eval_case_id=inference_result.eval_case_id,
        )

        if eval_case is None:
            raise ValueError(f"Eval case with id {inference_result.eval_case_id} not found for "
                             f"app {inference_result.app_name} and eval set "
                             f"{inference_result.eval_set_id}.")

        expected_invocations = self._build_expected_invocations_for_eval(eval_case)

        user_id, _ = self._get_user_id_from_eval_case(eval_case)
        session_app_name, session_app_name_set = self._get_session_app_name(eval_case, inference_result.app_name)
        # When user passed runner and case did not set app_name, use runner.app_name (same as inference)
        if self._runner is not None and not session_app_name_set:
            session_app_name = self._runner.app_name

        # Initialize result structures
        eval_metric_result_per_invocation = []
        overall_eval_metric_results = []

        # Pre-create EvalMetricResults entries for each invocation
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

        # Evaluate each metric
        case_error_message: Optional[str] = None
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
                    "Metric evaluation failed for metric `%s` for eval case id '%s'"
                    " with following error `%s`",
                    eval_metric.metric_name,
                    inference_result.eval_case_id,
                    e,
                    exc_info=True,
                )
                evaluation_result = EvaluationResult(overall_eval_status=EvalStatus.NOT_EVALUATED)

            # Track overall score
            reasons = []
            overall_rubric_scores = []
            for pr in evaluation_result.per_invocation_results:
                if pr.reason is not None:
                    reasons.append(pr.reason)
                if pr.rubric_scores:
                    overall_rubric_scores.extend(pr.rubric_scores)
            overall_reason = ";".join(reasons) if reasons else None
            overall_rubric = overall_rubric_scores if overall_rubric_scores else None
            overall_score = evaluation_result.overall_score
            overall_eval_metric_results.append(
                EvalMetricResult(
                    score=overall_score,
                    eval_status=evaluation_result.overall_eval_status,
                    metric_name=eval_metric.metric_name,
                    threshold=eval_metric.threshold,
                    criterion=eval_metric.criterion,
                    details=EvalMetricResultDetails(
                        reason=overall_reason,
                        score=overall_score,
                        rubric_scores=overall_rubric,
                    ) if (overall_reason is not None or overall_rubric is not None) else None,
                ))

            # Track per-invocation scores
            for idx, invocation in enumerate(eval_metric_result_per_invocation):
                if idx < len(evaluation_result.per_invocation_results):
                    invocation_result = evaluation_result.per_invocation_results[idx]
                else:
                    invocation_result = PerInvocationResult(actual_invocation=invocation.actual_invocation)
                inv_score = invocation_result.score
                invocation.eval_metric_results.append(
                    EvalMetricResult(
                        score=inv_score,
                        eval_status=invocation_result.eval_status,
                        metric_name=eval_metric.metric_name,
                        threshold=eval_metric.threshold,
                        criterion=eval_metric.criterion,
                        details=EvalMetricResultDetails(
                            reason=invocation_result.reason,
                            score=inv_score,
                            rubric_scores=invocation_result.rubric_scores,
                        ) if
                        (invocation_result.reason is not None or invocation_result.rubric_scores is not None) else None,
                    ))

        # Determine final status
        final_eval_status = self._generate_final_eval_status(overall_eval_metric_results)

        # Get session from same service as at inference (runner.session_service when user passed runner)
        session_service = (self._runner.session_service if self._runner is not None else self._session_service)
        session_details = await session_service.get_session(
            app_name=session_app_name,
            user_id=user_id,
            session_id=inference_result.session_id,
        )

        # Create result
        eval_case_result = EvalCaseResult(
            eval_set_id=inference_result.eval_set_id,
            eval_id=inference_result.eval_case_id,
            run_id=getattr(inference_result, "run_id", None),
            final_eval_status=final_eval_status,
            error_message=case_error_message,
            overall_eval_metric_results=overall_eval_metric_results,
            eval_metric_result_per_invocation=eval_metric_result_per_invocation,
            session_id=inference_result.session_id or "",
            session_details=session_details,
            user_id=user_id,
        )

        return (inference_result, eval_case_result)

    async def _evaluate_metric(
        self,
        eval_metric: EvalMetric,
        actual_invocations: list[Invocation],
        expected_invocations: Optional[list[Invocation]],
    ) -> EvaluationResult:
        """Evaluate a metric using the appropriate evaluator.

        Args:
            eval_metric: The metric to evaluate
            actual_invocations: Actual invocations from the agent
            expected_invocations: Expected invocations (optional)

        Returns:
            EvaluationResult with scores
        """
        evaluator = self._evaluator_registry.get_evaluator(eval_metric)

        if inspect.iscoroutinefunction(evaluator.evaluate_invocations):
            return await evaluator.evaluate_invocations(
                actual_invocations=actual_invocations,
                expected_invocations=expected_invocations,
            )
        else:
            return evaluator.evaluate_invocations(
                actual_invocations=actual_invocations,
                expected_invocations=expected_invocations,
            )

    def _generate_final_eval_status(self, overall_eval_metric_results: list[EvalMetricResult]) -> EvalStatus:
        """Determine final evaluation status from all metrics.

        Args:
            overall_eval_metric_results: Results for all metrics

        Returns:
            Final evaluation status
        """
        final_eval_status = EvalStatus.NOT_EVALUATED

        for result in overall_eval_metric_results:
            if result.eval_status == EvalStatus.PASSED:
                final_eval_status = EvalStatus.PASSED
            elif result.eval_status == EvalStatus.FAILED:
                return EvalStatus.FAILED  # Any failure means overall failure

        return final_eval_status

    async def _perform_inference_single_eval_item(
        self,
        app_name: str,
        eval_set_id: str,
        eval_case: EvalCase,
        root_agent: BaseAgent,
        session_id: Optional[str] = None,
    ) -> InferenceResult:
        """Perform inference for a single eval case.

        Args:
            app_name: Application name
            eval_set_id: Eval set ID
            eval_case: The eval case to run
            root_agent: The agent to evaluate
            session_id: Optional session ID (e.g. from BeforeInferenceCase callback context)

        Returns:
            InferenceResult with generated invocations
        """
        if session_id is None:
            session_id = self._session_id_supplier()
        inference_result = InferenceResult(
            app_name=app_name,
            eval_set_id=eval_set_id,
            eval_case_id=eval_case.eval_id,
            session_id=session_id,
        )

        try:
            if eval_case.eval_mode == EvalModeTrace:
                inferences = self._inference_trace_mode(eval_case, session_id)
            else:
                if eval_case.actual_conversation:
                    raise ValueError(
                        f"inference eval case (eval_case_id={eval_case.eval_id}, session_id={session_id}): "
                        "actual_conversation is only supported in trace mode")
                inferences = await self._generate_inferences_from_agent(
                    agent=root_agent,
                    eval_case=eval_case,
                    session_id=session_id,
                    app_name=app_name,
                )

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

    def _inference_trace_mode(self, eval_case: EvalCase, session_id: str) -> list[Invocation]:
        """Use pre-recorded conversation as inference result (trace mode). No agent run."""
        if eval_case.actual_conversation:
            if eval_case.conversation and len(eval_case.actual_conversation) != len(eval_case.conversation):
                raise ValueError(f"inference eval case (eval_case_id={eval_case.eval_id}, session_id={session_id}): "
                                 f"actual_conversation length {len(eval_case.actual_conversation)} does not match "
                                 f"conversation length {len(eval_case.conversation)}")
            for i, inv in enumerate(eval_case.actual_conversation):
                if inv is None:
                    raise ValueError(
                        f"inference eval case (eval_case_id={eval_case.eval_id}, session_id={session_id}): "
                        f"actual_conversation invocation is nil at index {i}")
                if inv.user_content is None:
                    raise ValueError(
                        f"inference eval case (eval_case_id={eval_case.eval_id}, session_id={session_id}): "
                        f"actual_conversation invocation user_content is nil at index {i}")
            return list(eval_case.actual_conversation)
        if not eval_case.conversation:
            raise ValueError(f"inference eval case (eval_case_id={eval_case.eval_id}, session_id={session_id}): "
                             "trace mode invocations are empty")
        return list(eval_case.conversation)

    def _trace_expecteds_for_eval(self, conversation: list[Invocation]) -> list[Invocation]:
        """Build placeholder expected invocations with only user input (trace mode, no reference answer)."""
        result = []
        for inv in conversation:
            if inv is None:
                result.append(
                    Invocation(
                        invocation_id="",
                        user_content=Content(parts=[]),
                        final_response=None,
                        intermediate_data=None,
                        creation_timestamp=0.0,
                    ))
            else:
                result.append(
                    Invocation(
                        invocation_id=inv.invocation_id,
                        user_content=inv.user_content,
                        final_response=None,
                        intermediate_data=None,
                        creation_timestamp=inv.creation_timestamp,
                    ))
        return result

    def _build_expected_invocations_for_eval(self, eval_case: EvalCase) -> Optional[list[Invocation]]:
        """Build expected invocations for evaluation. Trace mode: conversation as expected or placeholders."""
        if eval_case.eval_mode == EvalModeTrace:
            if eval_case.conversation:
                if eval_case.actual_conversation:
                    return list(eval_case.conversation)
                return self._trace_expecteds_for_eval(eval_case.conversation)
            if eval_case.actual_conversation:
                return self._trace_expecteds_for_eval(eval_case.actual_conversation)
            return None
        if eval_case.conversation:
            return eval_case.conversation
        return None

    async def _generate_inferences_from_agent(
        self,
        agent: BaseAgent,
        eval_case: EvalCase,
        session_id: str,
        app_name: str,
    ) -> list[Invocation]:
        """Generate invocations from an agent for an eval case.

        Args:
            agent: The agent to run
            eval_case: The eval case
            session_id: Session ID
            app_name: Application name (used for session storage)

        Returns:
            List of generated invocations
        """
        # Use UserSimulatorProvider to get the appropriate simulator
        user_simulator = self._user_simulator_provider.provide(eval_case)

        user_id, _ = self._get_user_id_from_eval_case(eval_case)
        initial_state, _ = self._get_initial_state_from_eval_case(eval_case)
        session_app_name, session_app_name_set = self._get_session_app_name(eval_case, app_name)

        if self._runner is not None:
            runner = self._runner
            if eval_case.context_messages:
                runner = Runner(
                    app_name=runner.app_name,
                    agent=runner.agent,
                    session_service=EvalSessionService(
                        runner.session_service,
                        context_messages=eval_case.context_messages,
                    ),
                    artifact_service=runner.artifact_service,
                    memory_service=runner.memory_service,
                )
        else:
            runner = Runner(
                app_name=session_app_name,
                agent=agent,
                session_service=EvalSessionService(
                    self._session_service,
                    context_messages=eval_case.context_messages,
                ),
                artifact_service=self._artifact_service,
                memory_service=self._memory_service,
            )

        if eval_case.session_input is not None or eval_case.context_messages:
            # When user passes runner and case does not set app_name, use runner.app_name
            app_name_for_session = (runner.app_name if
                                    (self._runner is not None and not session_app_name_set) else session_app_name)
            await runner.session_service.create_session(
                app_name=app_name_for_session,
                user_id=user_id,
                session_id=session_id,
                state=initial_state,
            )

        # Run conversation
        invocations = []
        agent_context = new_agent_context()

        while True:
            # Get next user message
            events_so_far = []
            next_message_result = await user_simulator.get_next_user_message(events_so_far)

            if next_message_result.status != Status.SUCCESS:
                break

            user_message = next_message_result.user_message

            # Run agent
            events = []
            final_response = None
            intermediate_events = []
            tool_uses = []
            tool_responses = []
            intermediate_responses = []

            async for event in runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    new_message=user_message,
                    agent_context=agent_context,
            ):
                events.append(event)

                # Collect intermediate events
                if not event.is_final_response():
                    intermediate_events.append(InvocationEvent(
                        author=event.author,
                        content=event.content,
                    ))

                    # Extract tool calls and responses from event content
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.function_call:
                                tool_uses.append(part.function_call)
                            if part.function_response:
                                tool_responses.append(part.function_response)
                            # Collect intermediate text responses
                            if part.text and event.author:
                                intermediate_responses.append((event.author, [part]))
                else:
                    if event.content and event.content.parts:
                        if any(part.text for part in event.content.parts if part.text):
                            final_response = event.content

            # Create invocation with IntermediateData
            intermediate_data = IntermediateData(
                tool_uses=tool_uses,
                tool_responses=tool_responses,
                intermediate_responses=intermediate_responses,
            )

            invocation = Invocation(
                invocation_id=f"inv_{len(invocations)}",
                user_content=user_message,
                final_response=final_response,
                intermediate_data=intermediate_data,
                creation_timestamp=time.time(),
            )

            invocations.append(invocation)

        return invocations
