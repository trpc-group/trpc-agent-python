# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
"""LangGraph utilities for TRPC Agent framework."""

import functools
import inspect
from typing import Any
from typing import Dict
from typing import Optional

from google.genai import types
from langchain_core.messages import AIMessage
from langchain_core.runnables.config import RunnableConfig

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.telemetry import trace_call_llm
from trpc_agent_sdk.telemetry import trace_tool_call
from trpc_agent_sdk.telemetry import tracer
from trpc_agent_sdk.tools import BaseTool

# Private string literal constants
TRPC_AGENT_KEY = "__trpc_agent__"
AGENT_CTX_KEY = "ctx"
LANGGRAPH_KEY = "langgraph"
STREAM_MODE_KEY = "stream_mode"
CHUNK_KEY = "chunk"


def get_agent_context(config: Dict[str, Any]) -> InvocationContext:
    """Extract InvocationContext from runnable config or similar structure.

    Args:
        config: Dictionary that may contain __trpc_agent__ key with context

    Returns:
        InvocationContext from the config

    Raises:
        ValueError: If InvocationContext is not found in the config
    """
    if isinstance(config, dict) and "configurable" in config and TRPC_AGENT_KEY in config["configurable"]:
        trpc_agent_data = config["configurable"][TRPC_AGENT_KEY]
        if isinstance(trpc_agent_data, dict) and AGENT_CTX_KEY in trpc_agent_data:
            return trpc_agent_data[AGENT_CTX_KEY]

    raise ValueError(f"InvocationContext not found in config. Make sure the config contains "
                     f"'{TRPC_AGENT_KEY}' with '{AGENT_CTX_KEY}' key.")


def get_langgraph_payload(event: Event) -> Optional[Dict[str, Any]]:
    """Extract stream mode and chunk from a LangGraph event.

    Args:
        event: The event to extract information from

    Returns:
        Dictionary containing langgraph data with stream_mode and chunk,
        or None if no LangGraph stream data is found.
    """
    if event.custom_metadata and LANGGRAPH_KEY in event.custom_metadata:
        return event.custom_metadata[LANGGRAPH_KEY]

    return None


def _ensure_config_parameter(func):
    """Private helper to ensure a function has a config parameter.

    If the function doesn't have a config parameter, this creates a new function
    with the config parameter added to the signature.

    Args:
        func: The function to check and potentially modify

    Returns:
        The original function if it has config parameter, or a new function with config parameter added
    """
    sig = inspect.signature(func)
    if "config" not in sig.parameters:
        # Create new signature with config parameter added
        new_params = list(sig.parameters.values())
        config_param = inspect.Parameter("config", inspect.Parameter.KEYWORD_ONLY, annotation=RunnableConfig)
        new_params.append(config_param)
        new_sig = sig.replace(parameters=new_params)

        # Create wrapper with new signature
        @functools.wraps(func)
        def wrapper_with_config(*args, **kwargs):
            # Extract config from kwargs, pass remaining args to original function
            kwargs.pop("config", None)  # Remove config since original function doesn't expect it
            # Call original function without config parameter
            result = func(*args, **kwargs)
            return result

        # Apply the new signature and update annotations
        wrapper_with_config.__signature__ = new_sig

        # Update __annotations__ to include the config parameter for Pydantic compatibility
        if not hasattr(wrapper_with_config, "__annotations__"):
            wrapper_with_config.__annotations__ = {}
        wrapper_with_config.__annotations__.update(getattr(func, "__annotations__", {}))
        wrapper_with_config.__annotations__["config"] = RunnableConfig

        logger.debug("Added 'config: RunnableConfig' parameter to function '%s'", func.__name__)
        return wrapper_with_config
    else:
        return func


def _build_llm_request(input_messages: list) -> LlmRequest:
    """Build LlmRequest from input messages.

    Args:
        input_messages: List of input messages

    Returns:
        LlmRequest object with converted messages
    """
    input_contents = []
    for msg in input_messages:
        if hasattr(msg, "content") and msg.content:
            if hasattr(msg, "type"):
                if msg.type == "human":
                    input_contents.append(types.Content(role="user", parts=[types.Part.from_text(text=msg.content)]))
                elif msg.type == "ai":
                    input_contents.append(types.Content(role="model", parts=[types.Part.from_text(text=msg.content)]))
                elif msg.type == "system":
                    input_contents.append(
                        types.Content(role="user", parts=[types.Part.from_text(text=f"System: {msg.content}")]))
    if not input_contents:
        return None
    return LlmRequest(
        model="langgraph-llm",
        config=types.GenerateContentConfig(),
        contents=input_contents,
    )


def _build_llm_response(result: Any, output_key: str = "messages") -> Optional[LlmResponse]:
    """Build LLM response from function result.

    Args:
        result: The result from the LLM function call (always a dict with messages)
        output_key: The key to use for extracting messages from result dict

    Returns:
        LlmResponse object for tracing, or None if no valid response found
    """
    logger.debug("Building LLM response from result: %s", result)

    # Extract the messages from the result dict using the specified key
    if not isinstance(result, dict) or output_key not in result:
        return None

    messages = result[output_key]
    if not isinstance(messages, list) or not messages:
        return None

    # Always use the last element from the messages list
    last_message = messages[-1]

    # Process the last message if it's an AIMessage
    if isinstance(last_message, AIMessage):
        # Create content for LlmResponse
        parts = []
        usage_metadata = None

        # Extract usage metadata if available
        if hasattr(last_message, "usage_metadata") and last_message.usage_metadata:
            usage_data = last_message.usage_metadata
            usage_metadata = types.GenerateContentResponseUsageMetadata(
                prompt_token_count=usage_data.get("input_tokens", 0),
                candidates_token_count=usage_data.get("output_tokens", 0),
                total_token_count=usage_data.get("total_tokens", 0),
            )

        # Handle tool calls or text content
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            for tool_call in last_message.tool_calls:
                parts.append(
                    types.Part.from_function_call(name=tool_call.get("name", ""), args=tool_call.get("args", {})))
        elif last_message.content:
            parts.append(types.Part.from_text(text=last_message.content))

        if parts:
            # Create and return the LlmResponse
            return LlmResponse(
                content=types.Content(role="model", parts=parts),
                usage_metadata=usage_metadata,
                partial=False,
            )

    return None


def langgraph_llm_node(llm_func=None, *, input_key: str = "messages", output_key: str = "messages"):
    """Decorator to wrap LLM functions with TRPC tracing.

    This wrapper automatically adds LLM tracing for functions that call LLMs directly.
    Can be used on any function that calls an LLM and returns a response.

    The decorated function should have signature: func(state: State, config: RunnableConfig)
    where State contains a field with the conversation history.

    Args:
        llm_func: The function that calls an LLM
        input_key: The key to use for extracting input messages from state dict (default: "messages")
        output_key: The key to use for extracting output messages from result dict (default: "messages")

    Returns:
        Wrapped function with automatic LLM tracing

    Example:
        @langgraph_llm_node
        def chatbot(state: State, config: RunnableConfig):
            return {"messages": [llm.invoke(state["messages"])]}

        # With custom keys:
        @langgraph_llm_node(input_key="conversation", output_key="response")
        def chatbot(state: State, config: RunnableConfig):
            return {"response": [llm.invoke(state["conversation"])]}
    """

    def decorator(func):
        # Ensure function has config parameter
        actual_func = _ensure_config_parameter(func)

        @functools.wraps(actual_func)
        def wrapper(*args, **kwargs):
            # Check if config is available for tracing
            logger.debug("LLM function '%s' executed with args: %s, kwargs: %s", actual_func.__name__, args, kwargs)
            config = kwargs.get("config")
            # Use tracer span when config is available
            with tracer.start_as_current_span("call_llm"):
                try:
                    # Extract state from first argument
                    state = args[0] if args else {}
                    input_messages = state.get(input_key, []) if isinstance(state, dict) else []

                    # Call the actual function
                    result = actual_func(*args, **kwargs)

                    # Add LLM tracing
                    ctx = get_agent_context(config)

                    # Build LlmRequest from input messages
                    llm_request = _build_llm_request(input_messages)

                    # Build LLM response with custom output key
                    llm_response = _build_llm_response(result, output_key)

                    # Invoke tracing if we have a valid response
                    if llm_request and llm_response:
                        trace_call_llm(ctx, Event.new_id(), llm_request, llm_response)
                        logger.debug("Added LLM trace for function: %s", actual_func.__name__)

                    return result

                except Exception as ex:  # pylint: disable=broad-except
                    logger.error("Could not trace LLM call in %s: %s", actual_func.__name__, ex)
                    # Still call the original function even if tracing fails
                    return actual_func(*args, **kwargs)

        return wrapper

    # Support both @langgraph_llm_node and @langgraph_llm_node() syntax
    if llm_func is None:
        # Called as @langgraph_llm_node(input_key="...", output_key="...")
        return decorator
    else:
        # Called as @langgraph_llm_node
        return decorator(llm_func)


def langgraph_tool_node(tool_func):
    """Decorator to wrap tool functions with TRPC tracing.

    This wrapper adds automatic tool tracing for tool functions.
    Should be used AFTER the @tool decorator from LangChain.

    Args:
        tool_func: The tool function to wrap

    Returns:
        Wrapped function with automatic tool tracing

    Example:
        @tool
        @langgraph_tool_node
        def calculate(operation: str, a: float, b: float) -> str:
            # Tool implementation
            return result
    """

    # Ensure function has config parameter
    actual_func = _ensure_config_parameter(tool_func)

    @functools.wraps(actual_func)
    def wrapper(*args, **kwargs):
        # Check if config is available for tracing
        config = kwargs.get("config")
        # Use tracer span when config is available
        with tracer.start_as_current_span(f"execute_tool {actual_func.__name__}"):
            try:
                # Call the actual function
                result = actual_func(*args, **kwargs)

                # Add tool tracing
                ctx = get_agent_context(config)

                # Create a mock tool for tracing
                class ToolTracingMetadata(BaseTool):

                    def __init__(self, name: str, description: str):
                        self._name = name
                        self.description = description

                    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
                        return

                tool_metadata = ToolTracingMetadata(actual_func.__name__, actual_func.__doc__)

                # Create an Event for the tool response
                response_data = {"result": result} if not isinstance(result, dict) else result
                parts = [types.Part.from_function_response(name=actual_func.__name__, response=response_data)]

                event = Event(
                    invocation_id=ctx.invocation_id,
                    author=ctx.agent_name,
                    branch=ctx.branch,
                    content=types.Content(role="model", parts=parts),
                    partial=False,
                )

                # Add tool tracing with kwargs as args
                trace_tool_call(tool_metadata, kwargs, event)
                logger.debug("Added tool trace for: %s, with args: %s", actual_func.__name__, kwargs)

                return result

            except Exception as ex:  # pylint: disable=broad-except
                logger.error("Could not trace tool call in %s: %s", actual_func.__name__, ex)
                # Still call the original function even if tracing fails
                return actual_func(*args, **kwargs)

    return wrapper
