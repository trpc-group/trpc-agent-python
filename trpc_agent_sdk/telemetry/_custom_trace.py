# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
#
"""Custom trace reporter for ecosystem agents and user-defined custom agents.

This module provides the CustomTraceReporter class that can be used to trace
function calls, function responses, and LLM calls in custom agent implementations
such as RemoteA2AAgent, ClaudeAgent, or any user-defined custom agent.

Example usage:
    ```python
    from trpc_agent_sdk.telemetry import CustomTraceReporter

    class MyCustomAgent(BaseAgent):
        async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
            # Create a trace reporter for this agent
            reporter = CustomTraceReporter(
                agent_name=self.name,
                model_prefix="my_custom",  # e.g., "my_custom:agent_name"
            )

            async for event in self._process_events():
                # Report the event for tracing
                reporter.trace_event(ctx, event)
                yield event
    ```
"""

from __future__ import annotations

from typing import Any
from typing import Callable
from typing import Optional

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import GenerateContentConfig

from ._trace import trace_call_llm
from ._trace import trace_tool_call
from ._trace import tracer


class _SyntheticTool(BaseTool):
    """Synthetic tool for tracing tool calls in custom agents.

    This is a minimal implementation used only for tracing purposes.
    It does not perform actual tool execution.
    """

    def __init__(self, name: str, description: str = ""):
        super().__init__(name=name, description=description or f"Custom tool: {name}")

    async def _run_async_impl(self, *, tool_context, args) -> Any:
        """Not used - this tool is only for tracing."""
        raise NotImplementedError("Synthetic tool should not be executed")


class CustomTraceReporter:
    """Reusable trace reporter for custom agent implementations.

    This class encapsulates the tracing logic for function calls, function responses,
    and LLM calls. It can be used by any custom agent implementation to report
    telemetry data to OpenTelemetry.

    The reporter maintains internal state to track pending function calls and
    match them with their corresponding responses for proper tool tracing.
    LLM requests are created automatically from the invocation context when needed.

    Attributes:
        agent_name: The name of the agent using this reporter.
        model_prefix: Prefix for the model name in traces (e.g., "a2a", "claude").
        tool_description_prefix: Prefix for tool descriptions (e.g., "Remote A2A tool").
        pending_function_calls: Internal dict tracking function calls awaiting responses.

    Example:
        ```python
        reporter = CustomTraceReporter(
            agent_name="my_agent",
            model_prefix="custom",
            tool_description_prefix="My Custom tool",
        )

        # Trace each event - LLM request is created automatically
        for event in events:
            reporter.trace_event(ctx, event)
        ```
    """

    def __init__(
        self,
        agent_name: str,
        model_prefix: str = "custom",
        tool_description_prefix: str = "Custom tool",
        text_content_filter: Optional[Callable[[str], bool]] = None,
    ):
        """Initialize the CustomTraceReporter.

        Args:
            agent_name: The name of the agent using this reporter.
            model_prefix: Prefix for the model name in traces (default: "custom").
                         The full model name will be "{model_prefix}:{agent_name}".
            tool_description_prefix: Prefix for tool descriptions in traces
                                    (default: "Custom tool").
            text_content_filter: Optional callable to filter text content before tracing.
                                Returns True if the text should be traced, False otherwise.
                                If None, all non-empty text will be traced.
        """
        self.agent_name = agent_name
        self.model_prefix = model_prefix
        self.tool_description_prefix = tool_description_prefix
        self.text_content_filter = text_content_filter
        self.pending_function_calls: dict[str, dict[str, Any]] = {}

    def _create_synthetic_llm_request(self, ctx: InvocationContext) -> LlmRequest:
        """Create a synthetic LlmRequest from invocation context for tracing.

        Args:
            ctx: The invocation context.

        Returns:
            A synthetic LlmRequest for tracing purposes.
        """
        user_content = ctx.user_content
        contents = [user_content] if user_content else []
        return LlmRequest(
            model=f"{self.model_prefix}:{self.agent_name}",
            contents=contents,
            config=GenerateContentConfig(),
        )

    def _create_synthetic_llm_response(self, event: Event) -> LlmResponse:
        """Create a synthetic LlmResponse from event for tracing.

        Args:
            event: The event containing the response content.

        Returns:
            A synthetic LlmResponse for tracing purposes.
        """
        return LlmResponse(
            content=event.content if event else None,
            error_message=event.error_message if event else None,
        )

    def _trace_function_call(self, event: Event) -> None:
        """Record function calls for later tracing when response is received.

        Args:
            event: Event containing function call parts.
        """
        function_calls = event.get_function_calls()
        for func_call in function_calls:
            self.pending_function_calls[func_call.id] = {
                'name': func_call.name,
                'args': func_call.args or {},
                'id': func_call.id,
            }

    def _trace_function_response(self, event: Event) -> None:
        """Trace tool call when function response is received.

        Args:
            event: Event containing function response parts.
        """
        function_responses = event.get_function_responses()
        for func_response in function_responses:
            response_id = func_response.id
            if response_id in self.pending_function_calls:
                function_call_data = self.pending_function_calls[response_id]
                # Trace tool call with matched function_call/response pair
                with tracer.start_as_current_span(f"execute_tool {function_call_data['name']}"):
                    synthetic_tool = _SyntheticTool(
                        name=function_call_data['name'],
                        description=f"{self.tool_description_prefix}: {function_call_data['name']}",
                    )
                    trace_tool_call(
                        tool=synthetic_tool,
                        args=function_call_data['args'],
                        function_response_event=event,
                    )
                # Remove from pending after tracing
                del self.pending_function_calls[response_id]

    def _trace_llm_response(self, ctx: InvocationContext, event: Event) -> None:
        """Trace LLM call when complete text response is received.

        Args:
            ctx: Invocation context.
            event: Event containing the complete text response.
        """
        with tracer.start_as_current_span("call_llm"):
            llm_request = self._create_synthetic_llm_request(ctx)
            llm_response = self._create_synthetic_llm_response(event)
            instruction = getattr(ctx.agent, 'instruction', None)
            instruction_metadata = getattr(instruction, 'metadata', None) if instruction is not None else None
            trace_call_llm(
                invocation_context=ctx,
                event_id=event.id,
                llm_request=llm_request,
                llm_response=llm_response,
                instruction_metadata=instruction_metadata,
            )

    def _should_trace_text(self, text_content: str) -> bool:
        """Check if text content should be traced.

        Args:
            text_content: The text content to check.

        Returns:
            True if the text should be traced, False otherwise.
        """
        if not text_content:
            return False
        if self.text_content_filter is not None:
            return self.text_content_filter(text_content)
        return True

    def trace_event(
        self,
        ctx: InvocationContext,
        event: Event,
    ) -> None:
        """Process tracing for an event.

        This method handles tracing for function calls, function responses,
        and complete text responses (LLM calls). LLM requests are created
        automatically from the invocation context when needed.

        Args:
            ctx: Invocation context.
            event: The event to trace.
        """
        # Skip partial events
        if event.partial:
            return

        # Check for function_call (tool invocation request)
        if event.get_function_calls():
            self._trace_function_call(event)
            return

        # Check for function_response (tool invocation result)
        if event.get_function_responses():
            self._trace_function_response(event)
            return

        # Check for complete text content (final response)
        text_content = event.get_text()
        if self._should_trace_text(text_content):
            self._trace_llm_response(ctx, event)

    def reset(self) -> None:
        """Reset the reporter state.

        Clears pending function calls.
        Call this when starting a new agent execution.
        """
        self.pending_function_calls.clear()
