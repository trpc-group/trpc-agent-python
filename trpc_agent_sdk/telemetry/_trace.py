# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# Directly reuse the telemetry from adk-python
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
"""Trace module for TRPC Agent framework."""

from __future__ import annotations

import json
from typing import Any
from typing import Optional

from opentelemetry import trace
from pydantic import BaseModel

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import InstructionMetadata

tracer = trace.get_tracer("trpc.python.agent")

_trpc_agent_span_name: str = "trpc.python.agent"  # pylint: disable=invalid-name


def set_trpc_agent_span_name(span_name: str):
    """
    Set the span name for the trpc agent.
    Will be modified by debug server to satisfy the need of web ui
    """
    global _trpc_agent_span_name  # pylint: disable=invalid-name
    _trpc_agent_span_name = span_name


def get_trpc_agent_span_name() -> str:
    """
    Get the span name for the trpc agent.
    """
    global _trpc_agent_span_name  # pylint: disable=invalid-name
    return _trpc_agent_span_name


def _safe_json_serialize(obj) -> str:
    """Convert any Python object to a JSON-serializable type or string.

    Args:
        obj: The object to serialize.

    Returns:
        The JSON-serialized object string or <non-serializable> if the object cannot be serialized.
    """

    try:
        # Try direct JSON serialization first
        return json.dumps(obj, ensure_ascii=False, default=lambda o: "<not serializable>")
    except (TypeError, OverflowError):
        return "<not serializable>"


def trace_runner(
    app_name: str,
    user_id: str,
    session_id: str,
    invocation_context: InvocationContext,
    new_message: Optional[Content] = None,
    last_event: Optional[Event] = None,
    state_begin: Optional[dict[str, Any]] = None,
    state_end: Optional[dict[str, Any]] = None,
):
    """Traces runner execution.

    This function records details about the runner execution as
    attributes on the current OpenTelemetry span.

    Args:
        app_name: The application name of the runner.
        user_id: The user ID of the session.
        session_id: The session ID of the session.
        invocation_context: The invocation context for the current agent run.
        new_message: The new message that started this invocation.
        last_event: The last non-streaming event from the agent execution.
        state_begin: The state before the runner execution.
        state_end: The state after the runner execution.
    """
    global _trpc_agent_span_name  # pylint: disable=invalid-name
    span = trace.get_current_span()
    span.set_attribute("gen_ai.system", _trpc_agent_span_name)
    span.set_attribute("gen_ai.operation.name", "run_runner")
    span.set_attribute(f"{_trpc_agent_span_name}.runner.app_name", app_name)
    span.set_attribute(
        f"{_trpc_agent_span_name}.runner.name",
        f"[trpc-agent]: {app_name}/{invocation_context.agent.name}",
    )
    span.set_attribute(f"{_trpc_agent_span_name}.runner.user_id", user_id)
    span.set_attribute(f"{_trpc_agent_span_name}.runner.session_id", session_id)
    input_str = ""
    if new_message and new_message.parts:
        input_str = "\n".join([part.text or "" for part in new_message.parts])
    span.set_attribute(f"{_trpc_agent_span_name}.runner.input", input_str)
    output_str = ""
    if last_event and last_event.content and last_event.content.parts:
        output_str = "\n".join([part.text or "" for part in last_event.content.parts])
    span.set_attribute(f"{_trpc_agent_span_name}.runner.output", output_str)

    # Set state attributes for begin and end
    if state_begin is not None:
        span.set_attribute(f"{_trpc_agent_span_name}.state.begin", _safe_json_serialize(state_begin))

    if state_end is not None:
        span.set_attribute(f"{_trpc_agent_span_name}.state.end", _safe_json_serialize(state_end))


def trace_cancellation(
    app_name: str,
    user_id: str,
    session_id: str,
    invocation_context: InvocationContext,
    reason: str,
    new_message: Optional[Content] = None,
    last_event: Optional[Event] = None,
    partial_text: str = "",
    state_begin: Optional[dict[str, Any]] = None,
    state_partial: Optional[dict[str, Any]] = None,
):
    """Traces runner cancellation.

    This function records details about a cancelled runner execution as
    attributes on the current OpenTelemetry span.

    Args:
        app_name: The application name of the runner.
        user_id: The user ID of the session.
        session_id: The session ID of the session.
        invocation_context: The invocation context for the cancelled run.
        reason: The cancellation reason.
        new_message: The new message that started this invocation.
        last_event: The last non-streaming event before cancellation.
        partial_text: Accumulated partial text from streaming (if cancelled during streaming).
        state_begin: The state before the runner execution.
        state_partial: The partial state at cancellation point.
    """
    global _trpc_agent_span_name  # pylint: disable=invalid-name
    span = trace.get_current_span()

    # Set span status to ERROR for cancellation
    span.set_status(trace.StatusCode.ERROR, reason)

    # Standard gen_ai attributes
    span.set_attribute("gen_ai.system", _trpc_agent_span_name)
    span.set_attribute("gen_ai.operation.name", "run_runner_cancelled")

    # Runner context attributes
    span.set_attribute(f"{_trpc_agent_span_name}.runner.app_name", app_name)
    span.set_attribute(
        f"{_trpc_agent_span_name}.runner.name",
        f"[trpc-agent]: {app_name}/{invocation_context.agent.name}",
    )
    span.set_attribute(f"{_trpc_agent_span_name}.runner.user_id", user_id)
    span.set_attribute(f"{_trpc_agent_span_name}.runner.session_id", session_id)

    # Input (prefixed with [CANCELLED] marker)
    input_str = ""
    if new_message and new_message.parts:
        input_str += "\n".join([part.text or "" for part in new_message.parts])
    span.set_attribute(f"{_trpc_agent_span_name}.runner.input", input_str)

    # Output (prefixed with [CANCELLED] marker, includes partial text or last event)
    output_str = "[CANCELLED]\n"
    if partial_text:
        output_str += partial_text
    elif last_event and last_event.content and last_event.content.parts:
        output_str += "\n".join([part.text or "" for part in last_event.content.parts])
    span.set_attribute(f"{_trpc_agent_span_name}.runner.output", output_str)

    # Cancellation-specific attributes
    span.set_attribute(f"{_trpc_agent_span_name}.cancellation.reason", reason)
    span.set_attribute(f"{_trpc_agent_span_name}.cancellation.agent_name", invocation_context.agent.name)
    if invocation_context.branch:
        span.set_attribute(f"{_trpc_agent_span_name}.cancellation.branch", invocation_context.branch)

    # State attributes
    if state_begin is not None:
        span.set_attribute(f"{_trpc_agent_span_name}.state.begin", _safe_json_serialize(state_begin))
    if state_partial is not None:
        span.set_attribute(f"{_trpc_agent_span_name}.state.partial", _safe_json_serialize(state_partial))


def trace_agent(
    invocation_context: InvocationContext,
    agent_action: str = "",
    state_begin: Optional[dict[str, Any]] = None,
    state_end: Optional[dict[str, Any]] = None,
):
    """Traces agent execution.

    This function records details about the agent execution as
    attributes on the current OpenTelemetry span.

    Args:
        invocation_context: The invocation context for the current agent run.
        agent_action: The formatted output string containing all agent actions
                      (text, function calls, function responses).
        state_begin: The state before the agent run.
        state_end: The state after the agent run.
    """
    global _trpc_agent_span_name  # pylint: disable=invalid-name
    span = trace.get_current_span()
    span.set_attribute("gen_ai.system", _trpc_agent_span_name)
    span.set_attribute("gen_ai.operation.name", "run_agent")
    span.set_attribute(f"{_trpc_agent_span_name}.agent.name", invocation_context.agent.name)
    if invocation_context.session:
        span.set_attribute(f"{_trpc_agent_span_name}.agent.session_id", invocation_context.session.id)
        span.set_attribute(f"{_trpc_agent_span_name}.agent.user_id", invocation_context.session.user_id)

    input_str = ""
    if invocation_context.user_content and invocation_context.user_content.parts:
        input_str = "\n".join([part.text or "" for part in invocation_context.user_content.parts])
    span.set_attribute(f"{_trpc_agent_span_name}.agent.input", input_str)

    span.set_attribute(f"{_trpc_agent_span_name}.agent.output", agent_action)

    # Set state attributes for begin and end
    if state_begin is not None:
        span.set_attribute(f"{_trpc_agent_span_name}.state.begin", _safe_json_serialize(state_begin))

    if state_end is not None:
        span.set_attribute(f"{_trpc_agent_span_name}.state.end", _safe_json_serialize(state_end))


def trace_tool_call(
    tool: BaseTool,
    args: dict[str, Any],
    function_response_event: Event,
    state_begin: Optional[dict[str, Any]] = None,
    state_end: Optional[dict[str, Any]] = None,
):
    """Traces tool call.

    Args:
        tool: The tool that was called.
        args: The arguments to the tool call.
        function_response_event: The event with the function response details.
        state_begin: The state before the tool execution.
        state_end: The state after the tool execution.
    """
    global _trpc_agent_span_name  # pylint: disable=invalid-name
    span = trace.get_current_span()
    span.set_attribute("gen_ai.system", _trpc_agent_span_name)
    span.set_attribute("gen_ai.operation.name", "execute_tool")
    span.set_attribute("gen_ai.tool.name", tool.name)
    span.set_attribute("gen_ai.tool.description", tool.description)
    tool_call_id = "<not specified>"
    tool_response = "<not specified>"
    if function_response_event.content.parts:
        function_response = function_response_event.content.parts[0].function_response
        if function_response is not None:
            tool_call_id = function_response.id
            tool_response = function_response.response

    span.set_attribute("gen_ai.tool.call.id", tool_call_id)

    if not isinstance(tool_response, dict):
        tool_response = {"result": tool_response}
    report_tool_response = {}
    for k, v in tool_response.items():
        if isinstance(v, BaseModel):
            report_tool_response[k] = v.model_dump_json()
        else:
            report_tool_response[k] = v
    span.set_attribute(
        f"{_trpc_agent_span_name}.tool_call_args",
        _safe_json_serialize(args),
    )
    span.set_attribute(f"{_trpc_agent_span_name}.event_id", function_response_event.id)
    span.set_attribute(
        f"{_trpc_agent_span_name}.tool_response",
        _safe_json_serialize(report_tool_response),
    )
    # Setting empty llm request and response (as UI expect these) while not
    # applicable for tool_response.
    span.set_attribute(f"{_trpc_agent_span_name}.llm_request", "{}")
    span.set_attribute(
        f"{_trpc_agent_span_name}.llm_response",
        "{}",
    )

    # Set state attributes for begin and end
    if state_begin is not None:
        span.set_attribute(f"{_trpc_agent_span_name}.state.begin", _safe_json_serialize(state_begin))

    if state_end is not None:
        span.set_attribute(f"{_trpc_agent_span_name}.state.end", _safe_json_serialize(state_end))


def trace_merged_tool_calls(
    response_event_id: str,
    function_response_event: Event,
    state_begin: Optional[dict[str, Any]] = None,
    state_end: Optional[dict[str, Any]] = None,
):
    """Traces merged tool call events.

    Calling this function is not needed for telemetry purposes. This is provided
    for preventing /debug/trace requests (typically sent by web UI).

    Args:
        response_event_id: The ID of the response event.
        function_response_event: The merged response event.
        state_begin: The state before the tool execution.
        state_end: The state after the tool execution.
    """
    global _trpc_agent_span_name  # pylint: disable=invalid-name
    span = trace.get_current_span()
    span.set_attribute("gen_ai.system", _trpc_agent_span_name)
    span.set_attribute("gen_ai.operation.name", "execute_tool")
    span.set_attribute("gen_ai.tool.name", "(merged tools)")
    span.set_attribute("gen_ai.tool.description", "(merged tools)")
    span.set_attribute("gen_ai.tool.call.id", response_event_id)

    span.set_attribute(f"{_trpc_agent_span_name}.tool_call_args", "N/A")
    span.set_attribute(f"{_trpc_agent_span_name}.event_id", response_event_id)
    try:
        function_response_event_json = function_response_event.model_dumps_json(exclude_none=True)
    except Exception:  # pylint: disable=broad-except
        function_response_event_json = "<not serializable>"

    span.set_attribute(
        f"{_trpc_agent_span_name}.tool_response",
        function_response_event_json,
    )
    # Setting empty llm request and response (as UI expect these) while not
    # applicable for tool_response.
    span.set_attribute(f"{_trpc_agent_span_name}.llm_request", "{}")
    span.set_attribute(
        f"{_trpc_agent_span_name}.llm_response",
        "{}",
    )

    # Set state attributes for begin and end
    if state_begin is not None:
        span.set_attribute(f"{_trpc_agent_span_name}.state.begin", _safe_json_serialize(state_begin))

    if state_end is not None:
        span.set_attribute(f"{_trpc_agent_span_name}.state.end", _safe_json_serialize(state_end))


def trace_call_llm(
    invocation_context: InvocationContext,
    event_id: str,
    llm_request: LlmRequest,
    llm_response: LlmResponse,
    instruction_metadata: Optional[InstructionMetadata] = None,
):
    """Traces a call to the LLM.

    This function records details about the LLM request and response as
    attributes on the current OpenTelemetry span.

    Args:
        invocation_context: The invocation context for the current agent run.
        event_id: The ID of the event.
        llm_request: The LLM request object.
        llm_response: The LLM response object.
        instruction_metadata: Optional instruction provenance metadata (InstructionMetadata)
            with ``name``, ``version``, and ``labels`` attributes. When
            provided, these are written directly to the call_llm span for
            precise instruction-to-generation association.
    """
    global _trpc_agent_span_name  # pylint: disable=invalid-name
    span = trace.get_current_span()
    # Special standard Open Telemetry GenaI attributes that indicate
    # that this is a span related to a Generative AI system.
    span.set_attribute("gen_ai.system", _trpc_agent_span_name)
    span.set_attribute("gen_ai.operation.name", "call_llm")
    span.set_attribute("gen_ai.request.model", llm_request.model)
    span.set_attribute(f"{_trpc_agent_span_name}.invocation_id", invocation_context.invocation_id)
    span.set_attribute(f"{_trpc_agent_span_name}.session_id", invocation_context.session.id)
    span.set_attribute(f"{_trpc_agent_span_name}.event_id", event_id)
    # Consider removing once GenAI SDK provides a way to record this info.
    span.set_attribute(
        f"{_trpc_agent_span_name}.llm_request",
        _safe_json_serialize(_build_llm_request_for_trace(llm_request)),
    )

    try:
        llm_response_json = llm_response.model_dump_json(exclude_none=True)
    except Exception:  # pylint: disable=broad-except
        llm_response_json = "<not serializable>"

    span.set_attribute(
        f"{_trpc_agent_span_name}.llm_response",
        llm_response_json,
    )

    if llm_response.usage_metadata is not None:
        usage = llm_response.usage_metadata
        if usage.prompt_token_count and usage.total_token_count:
            span.set_attribute(
                "gen_ai.usage.input_tokens",
                usage.prompt_token_count,
            )
            output_tokens = usage.total_token_count - usage.prompt_token_count
            span.set_attribute(
                "gen_ai.usage.output_tokens",
                output_tokens,
            )

    if instruction_metadata is not None:
        span.set_attribute(f"{_trpc_agent_span_name}.instruction.name", instruction_metadata.name)
        span.set_attribute(f"{_trpc_agent_span_name}.instruction.version", instruction_metadata.version)
        span.set_attribute(f"{_trpc_agent_span_name}.instruction.labels", ",".join(instruction_metadata.labels))


def _build_llm_request_for_trace(llm_request: LlmRequest) -> dict[str, Any]:
    """Builds a dictionary representation of the LLM request for tracing.

    This function prepares a dictionary representation of the LlmRequest
    object, suitable for inclusion in a trace. It excludes fields that cannot
    be serialized (e.g., function pointers) and avoids sending bytes data.

    Args:
      llm_request: The LlmRequest object.

    Returns:
      A dictionary representation of the LLM request.
    """
    # Some fields in LlmRequest are function pointers and can not be serialized.
    result = {
        "model": llm_request.model,
        "config": llm_request.config.model_dump(exclude_none=True, exclude="response_schema"),
        "contents": [],
    }
    # We do not want to send bytes data to the trace.
    for content in llm_request.contents:
        parts = [part for part in content.parts if not part.inline_data]
        result["contents"].append(Content(role=content.role, parts=parts).model_dump(exclude_none=True))
    return result
