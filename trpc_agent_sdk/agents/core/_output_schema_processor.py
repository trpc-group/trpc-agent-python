# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Output schema processor for TRPC Agent framework."""

from __future__ import annotations

import json

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.tools import SetModelResponseTool
from trpc_agent_sdk.tools import TOOL_NAME as SET_MODEL_RESPONSE_TOOL_NAME
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


class OutputSchemaRequestProcessor:
    """Processor that handles output schema for agents with tools."""

    async def run_async(self, invocation_context: InvocationContext, llm_request: LlmRequest) -> None:
        """Process output schema when tools are also present."""
        from trpc_agent_sdk.agents import LlmAgent

        agent = invocation_context.agent
        if not isinstance(agent, LlmAgent):
            return

        # Check if we need the processor: must have BOTH output_schema AND tools
        # If no tools, the native set_output_schema is used in _request_processor
        if not agent.output_schema or not agent.tools:
            return

        # Check if tool already in request (from previous processor run in same request)
        # We check llm_request.tools_dict instead of agent.tools to avoid mutating the agent
        tool_injected = False
        if llm_request.tools_dict:
            tool_injected = SET_MODEL_RESPONSE_TOOL_NAME in llm_request.tools_dict

        if not tool_injected:
            # Add the set_model_response tool to the REQUEST, not agent.tools
            # This ensures we don't mutate the agent object and avoid duplicate tools
            set_response_tool = SetModelResponseTool(agent.output_schema)
            agent.tools.append(set_response_tool)
            llm_request.append_tools([set_response_tool])

        # Add instruction about using the set_model_response tool
        instruction = ("IMPORTANT: You have access to other tools, but you must provide "
                       "your final response using the set_model_response tool with the "
                       "required structured format. After using any other tools needed "
                       "to complete the task, always call set_model_response with your "
                       "final answer in the specified schema format.\n\n"
                       "CRITICAL: When calling set_model_response, provide the fields "
                       "directly as parameters (not nested in objects). The schema expects "
                       "flat field names matching the output schema definition.")
        llm_request.append_instructions([instruction])

        logger.debug("Added output schema processor for agent: %s", agent.name)


# Create a default instance for convenience
default_output_schema_processor = OutputSchemaRequestProcessor()


def create_final_model_response_event(invocation_context: InvocationContext, json_response: str) -> Event:
    """Create a final model response event from structured JSON response.

    Args:
        invocation_context: The invocation context.
        json_response: The JSON response from set_model_response tool.

    Returns:
        A new Event that looks like a normal model response.
    """
    # Create a proper model response event
    final_event = Event(
        invocation_id=invocation_context.invocation_id,
        author=invocation_context.agent.name,
        branch=invocation_context.branch,
    )
    final_event.content = Content(role="model", parts=[Part(text=json_response)])
    return final_event


def get_structured_model_response(function_response_event: Event) -> str | None:
    """Extract structured model response from function response event.

    Args:
        function_response_event: The function response event to check.

    Returns:
        JSON response string if set_model_response was called, None otherwise.
    """
    if not function_response_event or not function_response_event.get_function_responses():
        return None

    for func_response in function_response_event.get_function_responses():
        if func_response.name == SET_MODEL_RESPONSE_TOOL_NAME:
            # Convert dict to JSON string
            return json.dumps(func_response.response, ensure_ascii=False)

    return None
