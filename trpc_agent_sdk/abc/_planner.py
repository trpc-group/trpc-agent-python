# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# Directly reuse the types from adk-python
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
"""Base Planner module for TRPC Agent framework.

This module provides the abstract base class for all planners in the system.
Planners guide agent thinking and action execution through structured approaches.
"""

from __future__ import annotations

import abc
from abc import ABC
from typing import List
from typing import Optional
from typing import TYPE_CHECKING

from google.genai.types import Part

from ._request import RequestABC

if TYPE_CHECKING:
    from trpc_agent_sdk.context import InvocationContext


class PlannerABC(ABC):
    """Abstract base class for all planners.

    The planner allows the agent to generate plans for queries to guide its
    action execution. Planners provide two main capabilities:
    1. Adding planning instructions to LLM requests
    2. Processing LLM responses to filter and organize content
    """

    @abc.abstractmethod
    def build_planning_instruction(
        self,
        context: InvocationContext,
        llm_request: RequestABC,
    ) -> Optional[str]:
        """Builds the system instruction to be appended to the LLM request for planning.

        Args:
            context: The invocation context containing session and agent info
            llm_request: The LLM request being built (readonly for planning)

        Returns:
            The planning system instruction, or None if no instruction is needed
        """
        pass

    @abc.abstractmethod
    def process_planning_response(
        self,
        context: InvocationContext,
        response_parts: List[Part],
        is_partial: bool = False,
    ) -> Optional[List[Part]]:
        """Processes the LLM response for planning.

        This method can filter, reorganize, or modify the response parts to
        separate planning/reasoning content from final user-facing content.

        Args:
            context: The invocation context for state access
            response_parts: The LLM response parts (readonly)
            is_partial: Whether this is a partial response (streaming)

        Returns:
            The processed response parts, or None if no processing is needed
        """
