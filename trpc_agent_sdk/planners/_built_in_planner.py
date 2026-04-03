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
"""Built-in Planner module for TRPC Agent framework.

This module provides the BuiltInPlanner class which leverages the model's
native thinking capabilities rather than implementing custom planning logic.
"""

from __future__ import annotations

from typing import List
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.abc import PlannerABC as BasePlanner
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import ThinkingConfig


class BuiltInPlanner(BasePlanner):
    """The built-in planner that uses model's built-in thinking features.

    This planner leverages the model's native reasoning capabilities by
    applying thinking configuration to the LLM request. It delegates the
    planning and reasoning process to the model itself.

    Attributes:
        thinking_config: Config for model built-in thinking features. An error
            will be returned if this field is set for models that don't support
            thinking.
    """

    def __init__(self, *, thinking_config: ThinkingConfig):
        """Initializes the built-in planner.

        Args:
            thinking_config: Config for model built-in thinking features. An error
                will be returned if this field is set for models that don't support
                thinking.
        """
        self.thinking_config = thinking_config

    def apply_thinking_config(self, llm_request: "LlmRequest") -> None:
        """Applies the thinking config to the LLM request.

        Args:
            llm_request: The LLM request to apply the thinking config to
        """
        if self.thinking_config:
            # Initialize config if not present
            if not llm_request.config:
                llm_request.config = GenerateContentConfig()

            # Apply thinking configuration
            llm_request.config.thinking_config = self.thinking_config

    @override
    def build_planning_instruction(
        self,
        context: InvocationContext,
        llm_request: LlmRequest,
    ) -> Optional[str]:
        """Builds planning instruction for built-in thinking.

        For built-in planners, the thinking is handled by the model itself
        through the thinking config, so no additional instructions are needed.

        Args:
            context: The invocation context
            llm_request: The LLM request

        Returns:
            None since built-in thinking doesn't require custom instructions
        """
        # Apply thinking config to enable model's built-in thinking
        self.apply_thinking_config(llm_request)

        # No additional instruction needed since thinking is handled by the model
        return None

    @override
    def process_planning_response(
        self,
        context: InvocationContext,
        response_parts: List[Part],
        is_partial: bool = False,
    ) -> Optional[List[Part]]:
        """Processes the planning response for built-in thinking.

        For built-in planners, the model handles the thinking process internally,
        so no post-processing of response parts is needed.

        Args:
            context: The invocation context
            response_parts: The LLM response parts
            is_partial: Whether this is a partial response from streaming

        Returns:
            None since no post-processing is needed for built-in thinking
        """
        # Built-in thinking doesn't require response processing
        # The model handles internal reasoning automatically
        return None
