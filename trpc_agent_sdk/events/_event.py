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
"""Event class for TRPC Agent framework."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import ConfigDict
from pydantic import Field
from pydantic import alias_generators

from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.models import TOOL_STREAMING_ARGS
from trpc_agent_sdk.types import EventActions
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse

_EVENT_FLAG_MODEL_VISIBLE = 1 << 0
_EVENT_FLAG_SUMMARY = 1 << 1


class Event(LlmResponse):
    """Represents an event in a conversation between agents and users.

    It is used to store the content of the conversation, as well as the actions
    taken by the agents like function calls, etc.

    Attributes:
      invocation_id: Required. The invocation ID of the event. Should be non-empty
        before appending to a session.
      author: Required. "user" or the name of the agent, indicating who appended
        the event to the session.
      actions: The actions taken by the agent.
      long_running_tool_ids: The ids of the long running function calls.
      branch: The branch of the event.
      id: The unique identifier of the event.
      timestamp: The timestamp of the event.
      visible: Whether the event is visible to outside observers. Default is True.
      model_flags: Bit flags controlling model visibility and summary state.
      request_id: Optional request ID for tracking across system boundaries.
      parent_invocation_id: Optional parent invocation ID for nested agent executions.
      tag: Optional business-specific labels for filtering/routing.
      filter_key: Optional hierarchical event filtering identifier.
      requires_completion: Whether this event needs completion signaling.
      version: Version for handling compatibility issues.
      is_final_response: Whether the event is the final response of the agent.
      get_function_calls: Returns the function calls in the event.
      get_function_responses: Returns the function responses in the event.
      get_text: Returns the concatenated text content from all text parts.
    """

    model_config = ConfigDict(
        extra="forbid",
        ser_json_bytes="base64",
        val_json_bytes="base64",
        alias_generator=alias_generators.to_camel,
        populate_by_name=True,
    )
    """The pydantic model config."""

    invocation_id: str = ""
    """The invocation ID of the event. Should be non-empty before appending to a session."""

    author: str = ""
    """'user' or the name of the agent, indicating who appended the event to the
    session."""

    actions: EventActions = Field(default_factory=EventActions)
    """The actions taken by the agent."""

    long_running_tool_ids: Optional[set[str]] = None
    """Set of ids of the long running function calls.
    Agent client will know from this field about which function call is long running.
    only valid for function call event
    """

    branch: Optional[str] = None
    """The branch of the event.

    The format is like agent_1.agent_2.agent_3, where agent_1 is the parent of
    agent_2, and agent_2 is the parent of agent_3.

    Branch is used when multiple sub-agent shouldn't see their peer agents'
    conversation history.
    """

    request_id: Optional[str] = None
    """The request ID for tracking across system boundaries."""

    parent_invocation_id: Optional[str] = None
    """The parent invocation ID for nested agent executions.

    Useful for tracking parent-child relationships in multi-agent scenarios.
    """

    tag: Optional[str] = None
    """Business-specific labels for filtering and routing events.

    Can be used to annotate events with custom metadata for downstream processing.
    """

    filter_key: Optional[str] = None
    """Hierarchical event filtering identifier.

    Used for filtering events in multi-agent scenarios. Format follows branch-like
    structure with delimiters for hierarchical filtering.
    """

    requires_completion: bool = False
    """Indicates if this event needs completion signaling.

    Used by the flow to determine if additional completion handling is required.
    """

    version: int = 0
    """Version for handling compatibility issues.

    Allows the system to handle different event format versions gracefully.
    """

    # The following are computed fields.
    # Do not assign the ID. It will be assigned by the session.
    id: str = ""
    """The unique identifier of the event."""

    timestamp: float = Field(default_factory=lambda: datetime.now().timestamp())
    """The timestamp of the event."""

    visible: bool = True
    """Whether the event is visible when invoke runner.

    Useful for hiding message not want to show in CustomAgent.

    When set to False, the runner will skip yielding this event to keep it
    hidden from external visibility.
    """

    object: Optional[str] = None
    """Object type for event classification.

    Used to categorize events by their type (e.g., graph.node.start, graph.node.complete).
    Provides a standardized way to identify event types alongside the legacy event_type in state_delta.
    """

    model_flags: int = _EVENT_FLAG_MODEL_VISIBLE
    """Bit flags for event model-state control.

    - MODEL_VISIBLE flag controls whether this event can be seen by model history builders.
    - SUMMARY flag marks this event as a summary-generated event.
    """

    def model_post_init(self, __context):
        """Post initialization logic for the event."""
        # Generates a random ID for the event.
        if not self.id:
            self.id = Event.new_id()

    def is_final_response(self) -> bool:
        """Returns whether the event is the final response of the agent."""
        if self.actions.skip_summarization or self.long_running_tool_ids:
            return True
        return (not self.get_function_calls() and not self.get_function_responses() and not self.partial
                and not self.has_trailing_code_execution_result() and not self.has_trailing_executable_code())

    def is_model_visible(self) -> bool:
        """Returns whether the event should be visible to model history."""
        return bool(self.model_flags & _EVENT_FLAG_MODEL_VISIBLE)

    def is_summary_event(self) -> bool:
        """Returns whether the event is generated as a summary event."""
        return bool(self.model_flags & _EVENT_FLAG_SUMMARY)

    def set_model_visible(self, model_visible: bool) -> None:
        """Set whether this event can be seen by model history builders."""
        if model_visible:
            self.model_flags |= _EVENT_FLAG_MODEL_VISIBLE
        else:
            self.model_flags &= ~_EVENT_FLAG_MODEL_VISIBLE

    def set_summary_event(self, is_summary: bool = True) -> None:
        """Set whether this event is marked as a summary event."""
        if is_summary:
            self.model_flags |= _EVENT_FLAG_SUMMARY
        else:
            self.model_flags &= ~_EVENT_FLAG_SUMMARY

    def get_function_calls(self) -> list[FunctionCall]:
        """Returns the function calls in the event."""
        func_calls = []
        if self.content and self.content.parts:
            for part in self.content.parts:
                if part.function_call:
                    func_calls.append(part.function_call)
        return func_calls

    def get_function_responses(self) -> list[FunctionResponse]:
        """Returns the function responses in the event."""
        func_response = []
        if self.content and self.content.parts:
            for part in self.content.parts:
                if part.function_response:
                    func_response.append(part.function_response)
        return func_response

    def get_text(self) -> str:
        """Returns the concatenated text content from all text parts in the event.

        Returns:
            Concatenated text string from all text parts. Returns empty string if no text content.
        """
        if not self.content or not self.content.parts:
            return ""
        text_parts = [part.text for part in self.content.parts if part.text]
        return "".join(text_parts)

    def has_trailing_code_execution_result(self, ) -> bool:
        """Returns whether the event has a trailing code execution result."""
        if self.content:
            if self.content.parts:
                return self.content.parts[-1].code_execution_result is not None
        return False

    def has_trailing_executable_code(self) -> bool:
        """Returns whether the event contains any executable code part (code to be run)."""
        if not self.content or not self.content.parts:
            return False
        return any(p.executable_code is not None for p in self.content.parts)

    def is_error(self) -> bool:
        """Returns whether the event is an error."""
        return self.error_code is not None

    def is_streaming_tool_call(self) -> bool:
        """Returns whether the event is a streaming tool call event.

        Streaming tool calls are identified by:
        - event.partial = True
        - content contains at least one function_call part with streaming delta args

        Returns:
            True if this is a streaming tool call event, False otherwise.
        """
        if not self.partial:
            return False
        if not self.content or not self.content.parts:
            return False
        # Check if any part is a streaming tool call (has function_call with streaming delta)
        for part in self.content.parts:
            if part.function_call:
                args = part.function_call.args or {}
                if TOOL_STREAMING_ARGS in args:
                    return True
        return False

    @staticmethod
    def new_id():
        return str(uuid.uuid4())
