# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
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
"""TRPC Agent Evaluation Framework.

A comprehensive evaluation framework for TRPC agents, adapted from
Google ADK Python evaluation framework.

Main Components:
- agent_evaluator: Main entry point for evaluations
- EvalCase, EvalSet: Test case and set definitions
- Evaluators: Trajectory, ROUGE, and custom evaluators
- User Simulators: Static and LLM-backed user simulation
"""

# Main evaluation entry point
from ._agent_evaluator import AgentEvaluator
from ._agent_evaluator import PassNC
from ._common import EvalBaseModel
from ._criterion_registry import CRITERION_REGISTRY
from ._criterion_registry import CriterionRegistry
from ._criterion_registry import CriterionType
from ._eval_callbacks import AfterEvaluateCaseArgs
from ._eval_callbacks import AfterEvaluateSetArgs
from ._eval_callbacks import AfterInferenceCaseArgs
from ._eval_callbacks import AfterInferenceSetArgs
from ._eval_callbacks import BeforeEvaluateCaseArgs
from ._eval_callbacks import BeforeEvaluateSetArgs
from ._eval_callbacks import BeforeInferenceCaseArgs
from ._eval_callbacks import BeforeInferenceSetArgs
from ._eval_callbacks import Callback
from ._eval_callbacks import CallbackFn
from ._eval_callbacks import CallbackPoint
from ._eval_callbacks import CallbackResult
from ._eval_callbacks import Callbacks
from ._eval_callbacks import CallbacksRunner
from ._eval_callbacks import EvalSetRunResult
from ._eval_case import ConversationScenario
from ._eval_case import EvalCase
from ._eval_case import EvalModeTrace
from ._eval_case import IntermediateData
from ._eval_case import IntermediateDataType
from ._eval_case import Invocation
from ._eval_case import InvocationEvent
from ._eval_case import InvocationEvents
from ._eval_case import SessionInput
from ._eval_case import StaticConversation
from ._eval_case import get_all_tool_calls
from ._eval_case import get_all_tool_responses
from ._eval_config import EvalConfig
from ._eval_criterion import FinalResponseCriterion
from ._eval_criterion import JSONCriterion
from ._eval_criterion import TextCriterion
from ._eval_criterion import ToolTrajectoryCriterion
from ._eval_metrics import EvalMetric
from ._eval_metrics import EvalStatus
from ._eval_metrics import Interval
from ._eval_metrics import MetricInfo
from ._eval_metrics import MetricValueInfo
from ._eval_metrics import PrebuiltMetrics
from ._eval_pass import pass_at_k
from ._eval_pass import pass_hat_k
from ._eval_result import EvalCaseResult
from ._eval_result import EvalCaseResultSummary
from ._eval_result import EvalCaseRunSummary
from ._eval_result import EvalMetricResult
from ._eval_result import EvalMetricResultDetails
from ._eval_result import EvalMetricResultPerInvocation
from ._eval_result import EvalMetricRunSummary
from ._eval_result import EvalMetricSummary
from ._eval_result import EvalSetAggregateResult
from ._eval_result import EvalSetResult
from ._eval_result import EvalSetResultSummary
from ._eval_result import EvalSetRunSummary
from ._eval_result import EvalStatusCounts
from ._eval_result import EvaluateResult
from ._eval_result import EvaluationResult
from ._eval_result import PerInvocationResult
from ._eval_service_base import BaseEvalService
from ._eval_service_base import EvaluateConfig
from ._eval_service_base import EvaluateRequest
from ._eval_service_base import InferenceConfig
from ._eval_service_base import InferenceRequest
from ._eval_service_base import InferenceResult
from ._eval_service_base import InferenceStatus
from ._eval_session_service import EvalSessionService
from ._eval_set import EvalSet
from ._eval_set_results_manager_base import EvalSetResultsManager
from ._eval_set_results_manager_utils import build_eval_set_result_summary
from ._eval_set_results_manager_utils import create_eval_set_result
from ._eval_sets_manager_base import EvalSetsManager
from ._eval_sets_manager_utils import NotFoundError
from ._eval_sets_manager_utils import add_eval_case_to_eval_set
from ._eval_sets_manager_utils import delete_eval_case_from_eval_set
from ._eval_sets_manager_utils import get_eval_case_from_eval_set
from ._eval_sets_manager_utils import get_eval_set_from_app_and_id
from ._eval_sets_manager_utils import update_eval_case_in_eval_set
from ._evaluator_base import Evaluator
from ._evaluator_registry import EVALUATOR_REGISTRY
from ._evaluator_registry import EvaluatorRegistry
from ._final_response_evaluator import FinalResponseEvaluator
from ._in_memory_eval_sets_manager import InMemoryEvalSetsManager
from ._llm_criterion import DEFAULT_KNOWLEDGE_TOOL_NAMES
from ._llm_criterion import DEFAULT_NUM_SAMPLES
from ._llm_criterion import JudgeModelOptions
from ._llm_criterion import LLMJudgeCriterion
from ._llm_criterion import Rubric
from ._llm_criterion import RubricContent
from ._llm_criterion import RubricScore
from ._llm_criterion import ScoreResult
from ._llm_criterion import get_llm_criterion_from_metric
from ._llm_criterion import sanitize_criterion_for_export
from ._llm_evaluator import InvocationsAggregatorFn
from ._llm_evaluator import LLMEvaluatorRegistry
from ._llm_evaluator import LLMFinalResponseEvaluator
from ._llm_evaluator import LLMRubricKnowledgeRecallEvaluator
from ._llm_evaluator import LLMRubricResponseEvaluator
from ._llm_evaluator import LLM_EVALUATOR_REGISTRY
from ._llm_evaluator import LLM_METRIC_NAMES
from ._llm_evaluator import MessagesConstructorFn
from ._llm_evaluator import ResponseScorerFn
from ._llm_evaluator import SamplesAggregatorFn
from ._llm_judge import AverageInvocationsAggregator
from ._llm_judge import DefaultMessagesConstructor
from ._llm_judge import DefaultResponseScorer
from ._llm_judge import FinalResponseOutput
from ._llm_judge import InvocationsAggregator
from ._llm_judge import LLMJudge
from ._llm_judge import MajorityVoteSamplesAggregator
from ._llm_judge import MessagesConstructor
from ._llm_judge import ResponseScorer
from ._llm_judge import RubricItemOutput
from ._llm_judge import RubricJudgeOutput
from ._llm_judge import SamplesAggregator
from ._local_eval_service import LocalEvalService
from ._local_eval_set_results_manager import LocalEvalSetResultsManager
from ._local_eval_sets_manager import LocalEvalSetsManager
from ._local_eval_sets_manager import load_eval_set_from_file
from ._rouge_evaluator import RougeEvaluator
from ._static_user_simulator import StaticUserSimulator
from ._trajectory_evaluator import TrajectoryEvaluator
from ._user_simulator_base import BaseUserSimulatorConfig
from ._user_simulator_base import NextUserMessage
from ._user_simulator_base import Status
from ._user_simulator_base import UserSimulator
from ._user_simulator_provider import UserSimulatorProvider
from ._utils import EvalResultHandler
from ._utils import MetricRunRecord

__all__ = [
    "CRITERION_REGISTRY",
    "CriterionRegistry",
    "CriterionType",
    "AfterEvaluateCaseArgs",
    "AfterEvaluateSetArgs",
    "AfterInferenceCaseArgs",
    "AfterInferenceSetArgs",
    "BeforeEvaluateCaseArgs",
    "BeforeEvaluateSetArgs",
    "BeforeInferenceCaseArgs",
    "BeforeInferenceSetArgs",
    "Callback",
    "CallbackFn",
    "CallbackPoint",
    "CallbackResult",
    "Callbacks",
    "EvalSetRunResult",
    "ConversationScenario",
    "EvalCase",
    "EvalModeTrace",
    "IntermediateData",
    "IntermediateDataType",
    "Invocation",
    "InvocationEvent",
    "InvocationEvents",
    "SessionInput",
    "StaticConversation",
    "get_all_tool_calls",
    "get_all_tool_responses",
    "EvalConfig",
    "FinalResponseCriterion",
    "JSONCriterion",
    "TextCriterion",
    "ToolTrajectoryCriterion",
    "EvalMetric",
    "EvalStatus",
    "Interval",
    "MetricInfo",
    "MetricValueInfo",
    "PrebuiltMetrics",
    "EvalCaseResult",
    "EvalCaseResultSummary",
    "EvalCaseRunSummary",
    "EvalMetricResult",
    "EvalMetricResultDetails",
    "EvalMetricResultPerInvocation",
    "EvalMetricRunSummary",
    "EvalMetricSummary",
    "EvalSetAggregateResult",
    "EvalSetResult",
    "EvalSetResultSummary",
    "EvalSetRunSummary",
    "EvalStatusCounts",
    "EvaluateResult",
    "EvaluationResult",
    "PerInvocationResult",
    "BaseEvalService",
    "EvaluateConfig",
    "EvaluateRequest",
    "InferenceConfig",
    "InferenceRequest",
    "InferenceResult",
    "EvalSet",
    "EvalSetResultsManager",
    "EvalSetsManager",
    "Evaluator",
    "EVALUATOR_REGISTRY",
    "EvaluatorRegistry",
    "InMemoryEvalSetsManager",
    "LLMJudgeCriterion",
    "DEFAULT_KNOWLEDGE_TOOL_NAMES",
    "DEFAULT_NUM_SAMPLES",
    "RubricScore",
    "ScoreResult",
    "AverageInvocationsAggregator",
    "DefaultMessagesConstructor",
    "DefaultResponseScorer",
    "InvocationsAggregator",
    "MajorityVoteSamplesAggregator",
    "MessagesConstructor",
    "ResponseScorer",
    "SamplesAggregator",
    "LocalEvalSetResultsManager",
    "LocalEvalSetsManager",
    "BaseUserSimulatorConfig",
    "NextUserMessage",
    "Status",
    "UserSimulator",
    "UserSimulatorProvider",
    "AgentEvaluator",
    "PassNC",
    "FinalResponseEvaluator",
    "InvocationsAggregatorFn",
    "LLMFinalResponseEvaluator",
    "LLMRubricKnowledgeRecallEvaluator",
    "LLMRubricResponseEvaluator",
    "LLM_EVALUATOR_REGISTRY",
    "LLM_METRIC_NAMES",
    "MessagesConstructorFn",
    "ResponseScorerFn",
    "SamplesAggregatorFn",
    "LocalEvalService",
    "RougeEvaluator",
    "StaticUserSimulator",
    "TrajectoryEvaluator",
    "EvalBaseModel",
    "CallbacksRunner",
    "pass_at_k",
    "pass_hat_k",
    "InferenceStatus",
    "EvalSessionService",
    "build_eval_set_result_summary",
    "create_eval_set_result",
    "NotFoundError",
    "add_eval_case_to_eval_set",
    "delete_eval_case_from_eval_set",
    "get_eval_case_from_eval_set",
    "get_eval_set_from_app_and_id",
    "update_eval_case_in_eval_set",
    "JudgeModelOptions",
    "Rubric",
    "RubricContent",
    "get_llm_criterion_from_metric",
    "sanitize_criterion_for_export",
    "LLMEvaluatorRegistry",
    "FinalResponseOutput",
    "LLMJudge",
    "RubricItemOutput",
    "RubricJudgeOutput",
    "load_eval_set_from_file",
    "EvalResultHandler",
    "MetricRunRecord",
]
