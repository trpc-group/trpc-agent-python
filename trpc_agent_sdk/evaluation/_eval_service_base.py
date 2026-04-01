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
"""Base evaluation service interface.

"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from enum import Enum
from typing import AsyncGenerator
from typing import Optional

from pydantic import Field

from ._common import EvalBaseModel
from ._eval_case import Invocation
from ._eval_metrics import EvalMetric
from ._eval_result import EvalCaseResult


class EvaluateConfig(EvalBaseModel):
    """Configuration for running evaluations.

    Attributes:
        eval_metrics: List of metrics to evaluate
        parallelism: Number of parallel evaluations to run
    """

    eval_metrics: list[EvalMetric] = Field(description="The list of metrics to be used in Eval.", )

    parallelism: int = Field(
        default=4,
        description="""Number of parallel evaluations to run during an Eval. Few
factors to consider while changing this value:

1) Your available quota with the model, especially for those metrics that use
a model as a judge. Models tend to enforce per-minute or per-second SLAs. Using
a larger value could result in the eval quickly consuming the quota.
""",
    )


class InferenceConfig(EvalBaseModel):
    """Configuration for running inferences.

    Attributes:
        labels: User-defined metadata labels
        parallelism: Number of parallel inferences to run
    """

    labels: Optional[dict[str, str]] = Field(
        default=None,
        description="Labels with user-defined metadata to break down billed charges.",
    )

    parallelism: int = Field(
        default=4,
        description="""Number of parallel inferences to run during an Eval. Few
factors to consider while changing this value:

1) Your available quota with the model. Models tend to enforce per-minute or
per-second SLAs. Using a larger value could result in the eval quickly consuming
the quota.

2) The tools used by the Agent could also have their SLA. Using a larger value
could also overwhelm those tools.""",
    )


class InferenceRequest(EvalBaseModel):
    """Request to perform inferences for eval cases.

    This is the set-level request: it identifies which eval set to run and how.
    For each case, the actual app/session used at runtime may differ (e.g. from
    case session_input or runner); see InferenceResult.app_name and case-level
    callback args (e.g. session_app_name) for the effective app per case.

    Attributes:
        app_name: Set-level app name (see Field description).
        eval_set_id: ID of the eval set.
        eval_case_ids: Optional list of specific eval case IDs.
        inference_config: Configuration for inference.
    """

    app_name: str = Field(description="Set-level app name: used to load the eval set and as the default "
                          "for result tagging. The effective app for a single case may be overridden by "
                          "that case's session_input.app_name or by the runner's app_name.")

    eval_set_id: str = Field(description="Id of the eval set.")

    eval_case_ids: Optional[list[str]] = Field(
        default=None,
        description="""Id of the eval cases for which inferences need to be
generated.

All the eval case ids should belong to the EvalSet.

If the list of eval case ids are empty or not specified, then all the eval cases
in an eval set are evaluated.
      """,
    )

    inference_config: InferenceConfig = Field(description="The config to use for inference.", )


class InferenceStatus(Enum):
    """Status of an inference operation."""

    UNKNOWN = 0
    SUCCESS = 1
    FAILURE = 2


class InferenceResult(EvalBaseModel):
    """Results from inference a single eval case.

    Attributes:
        app_name: Name of the application
        eval_set_id: ID of the eval set
        eval_case_id: ID of the eval case
        inferences: List of invocations generated
        session_id: Session ID used
        status: Status of the inference
        error_message: Error message if failed
    """

    app_name: str = Field(description="The name of the app to which the eval case belongs to.")

    eval_set_id: str = Field(description="Id of the eval set.")

    eval_case_id: str = Field(description="Id of the eval case for which inferences were generated.", )

    inferences: Optional[list[Invocation]] = Field(
        default=None,
        description="Inferences obtained from the Agent for the eval case.",
    )

    session_id: Optional[str] = Field(default=None, description="Id of the inference session.")

    status: InferenceStatus = Field(
        default=InferenceStatus.UNKNOWN,
        description="Status of the inference.",
    )

    error_message: Optional[str] = Field(
        default=None,
        description="Error message if the inference failed.",
    )

    run_id: Optional[int] = Field(
        default=None,
        description="1-based run index when num_runs > 1.",
    )


class EvaluateRequest(EvalBaseModel):
    """Request to evaluate inference results.

    Attributes:
        inference_results: List of inference results to evaluate
        evaluate_config: Configuration for evaluation
    """

    inference_results: list[InferenceResult] = Field(description="A list of inferences that need to be evaluated.", )

    evaluate_config: EvaluateConfig = Field(description="The config to use for evaluations.", )


class BaseEvalService(ABC):
    """Abstract base class for evaluation services.

    This defines the interface for running evaluations in TRPC agents.
    """

    @abstractmethod
    async def perform_inference(
        self,
        inference_request: InferenceRequest,
    ) -> AsyncGenerator[InferenceResult, None]:
        """Generate inferences for eval cases.

        Args:
            inference_request: The request for generating inferences

        Yields:
            InferenceResult for each eval case
        """
        ...

    @abstractmethod
    async def evaluate(
        self,
        evaluate_request: EvaluateRequest,
    ) -> AsyncGenerator[EvalCaseResult, None]:
        """Evaluate inference results.

        Args:
            evaluate_request: The request to evaluate inferences

        Yields:
            EvalCaseResult for each evaluated case
        """
        ...
