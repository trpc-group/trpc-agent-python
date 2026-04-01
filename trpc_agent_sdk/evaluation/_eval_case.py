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
"""Evaluation case data structures.

This module defines the core data structures for evaluation cases,
adapted from Google ADK Python to work with TRPC Agent framework.
"""

from __future__ import annotations

from typing import Any
from typing import Literal
from typing import Optional
from typing import Union

from pydantic import Field
from pydantic import model_validator

from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part

from ._common import EvalBaseModel


class IntermediateData(EvalBaseModel):
    """Container for intermediate data during agent execution.

    Attributes:
        tool_uses: List of tool/function calls made by the agent
        tool_responses: List of tool/function responses received
        intermediate_responses: Intermediate text responses during execution
    """

    tool_uses: list[FunctionCall] = Field(default_factory=list)
    """Tool use trajectory in chronological order."""

    tool_responses: list[FunctionResponse] = Field(default_factory=list)
    """Tool response trajectory in chronological order."""

    intermediate_responses: list[tuple[str, list[Part]]] = Field(default_factory=list)
    """Intermediate responses generated during execution."""


class InvocationEvent(EvalBaseModel):
    """An event during agent invocation.

    Attributes:
        author: Name of the agent that generated this event
        content: Content of the event
    """

    author: str
    """The name of the agent that authored this event."""

    content: Optional[Content]
    """The content of the event."""


class InvocationEvents(EvalBaseModel):
    """Container for events during an invocation.

    Attributes:
        invocation_events: List of events that occurred
    """

    invocation_events: list[InvocationEvent] = Field(default_factory=list)
    """A list of invocation events."""


# Type alias for intermediate data
IntermediateDataType = Union[IntermediateData, InvocationEvents]


class Invocation(EvalBaseModel):
    """Represents a single invocation (user query + agent response).

    This is the basic unit for evaluation, containing:
    - User input
    - Agent's final response
    - Intermediate steps (tool calls, events)

    Attributes:
        invocation_id: Unique identifier for this invocation
        user_content: User's input content
        final_response: Agent's final response
        intermediate_data: Intermediate execution data
        creation_timestamp: When this invocation was created
    """

    invocation_id: str = ""
    """Unique identifier for the invocation."""

    user_content: Content
    """Content provided by the user in this invocation."""

    final_response: Optional[Content] = None
    """Final response from the agent."""

    intermediate_data: Optional[IntermediateDataType] = None
    """Intermediate steps generated during agent execution."""

    creation_timestamp: float = 0.0
    """Timestamp for the current invocation."""


class SessionInput(EvalBaseModel):
    """Values that help initialize a Session.

    Attributes:
        app_name: The name of the app
        user_id: The user id
        state: The initial state of the session
    """

    app_name: str
    """The name of the app."""

    user_id: str
    """The user id."""

    state: dict[str, Any] = Field(default_factory=dict)
    """The state of the session."""


# Type alias for static conversation
StaticConversation = list[Invocation]
"""A conversation where user queries are pre-defined."""

# Eval mode: default (run agent) or trace (use pre-recorded conversation as inference result)
EvalModeTrace: Literal["trace"] = "trace"


class ConversationScenario(EvalBaseModel):
    """Scenario for dynamic conversation with simulated user.

    Attributes:
        starting_prompt: Initial user message
        conversation_plan: Plan for the conversation flow
    """

    starting_prompt: str
    """Starting prompt for the conversation."""

    conversation_plan: str
    """A plan that user simulation system needs to follow."""


class EvalCase(EvalBaseModel):
    """An evaluation test case.

    Represents a complete test scenario with either:
    - Static conversation (pre-defined queries and expected responses)
    - Dynamic conversation scenario (for user simulation)
    - Trace mode: pre-recorded conversation as inference result (no agent run)

    Attributes:
        eval_id: Unique identifier for this test case
        eval_mode: "trace" = use conversation/actual_conversation as-is; default = run agent
        conversation: Static conversation (or expected in trace when actual_conversation is set)
        actual_conversation: Actual trace for evaluation (trace mode only, aligned by turn with conversation)
        conversation_scenario: Dynamic scenario for user simulation (non-trace only)
        session_input: Initial session state
    """

    eval_id: str
    """Unique identifier for the evaluation case."""

    eval_mode: Optional[str] = Field(default=None, alias="evalMode")
    """When \"trace\", inference uses pre-recorded conversation; actual_conversation allowed."""

    conversation: Optional[StaticConversation] = None
    """Static conversation; in trace mode with actual_conversation this is the expected."""

    actual_conversation: Optional[StaticConversation] = Field(default=None, alias="actualConversation")
    """Actual invocations for evaluation (trace mode only); aligned by turn with conversation when both set."""

    conversation_scenario: Optional[ConversationScenario] = None
    """A conversation scenario for user simulation (non-trace only)."""

    session_input: Optional[SessionInput] = None
    """Session input for initialization."""

    context_messages: Optional[list[Content]] = Field(default=None, alias="contextMessages")
    """Per-case context (Content: parts+role), prepended to conversation, not persisted."""

    creation_timestamp: float = 0.0
    """The time when this eval case was created."""

    @model_validator(mode="after")
    def ensure_conversation_xor_conversation_scenario(self) -> EvalCase:
        """Trace: conversation or actual_conversation (no scenario). Default: conversation xor conversation_scenario."""
        is_trace = self.eval_mode == EvalModeTrace
        if is_trace:
            if self.conversation_scenario is not None:
                raise ValueError("conversation_scenario is not allowed when eval_mode is \"trace\"")
            if not self.conversation and not self.actual_conversation:
                raise ValueError("trace mode requires at least one of conversation or actual_conversation")
            return self
        if (self.conversation is None) == (self.conversation_scenario is None):
            raise ValueError("Exactly one of conversation and conversation_scenario must be provided")
        return self


def get_all_tool_calls(intermediate_data: Optional[IntermediateDataType], ) -> list[FunctionCall]:
    """Extract all tool calls from intermediate data.

    Args:
        intermediate_data: The intermediate data to extract from

    Returns:
        List of function calls
    """
    if not intermediate_data:
        return []

    tool_calls = []
    if isinstance(intermediate_data, IntermediateData):
        tool_calls = intermediate_data.tool_uses
    elif isinstance(intermediate_data, InvocationEvents):
        for invocation_event in intermediate_data.invocation_events:
            if invocation_event.content and invocation_event.content.parts:
                for p in invocation_event.content.parts:
                    if p.function_call:
                        tool_calls.append(p.function_call)

    return tool_calls


def get_all_tool_responses(intermediate_data: Optional[IntermediateDataType], ) -> list[FunctionResponse]:
    """Extract all tool responses from intermediate data.

    Args:
        intermediate_data: The intermediate data to extract from

    Returns:
        List of function responses
    """
    if not intermediate_data:
        return []

    tool_responses = []
    if isinstance(intermediate_data, IntermediateData):
        tool_responses = intermediate_data.tool_responses
    elif isinstance(intermediate_data, InvocationEvents):
        for invocation_event in intermediate_data.invocation_events:
            if invocation_event.content and invocation_event.content.parts:
                for p in invocation_event.content.parts:
                    if p.function_response:
                        tool_responses.append(p.function_response)

    return tool_responses
