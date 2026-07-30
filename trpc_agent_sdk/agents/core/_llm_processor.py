# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""LLM Processor implementation for TRPC Agent framework.

This module provides the LlmProcessor class which serves as an adapter between
LlmAgent and the model system. It handles different types of LLM responses
and generates appropriate events for the agent to handle.

The LlmProcessor is simplified to directly create Event objects from LlmResponse.
"""

from __future__ import annotations

import time
from typing import AsyncGenerator
from typing import Optional

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.planners import default_planning_processor
from trpc_agent_sdk.telemetry import report_call_llm
from trpc_agent_sdk.telemetry import trace_call_llm
from trpc_agent_sdk.telemetry import tracer


class LlmProcessor:
    """LLM Processor for handling model communication and response processing.

    This class serves as an adapter between LlmAgent and the underlying model
    system, converting model responses to unified Events.
    """

    def __init__(self, model: LLMModel):
        """Initialize LlmProcessor with a specific model.

        Args:
            model: The LLM model to use for processing
        """
        self.model = model

    async def call_llm_async(self,
                             request: LlmRequest,
                             context: InvocationContext,
                             stream: bool = True) -> AsyncGenerator[Event, None]:
        """Call the LLM and yield Events for each response.

        This method:
        1. Validates the request
        2. Calls the model with the request
        3. Creates Event objects directly from LlmResponse
        4. Yields unified Event objects for the agent to handle

        Args:
            request: The model request to send
            context: The invocation context
            stream: Whether to stream responses

        Yields:
            Event: Events representing the model responses
        """
        author = context.agent.name
        logger.debug("Starting LLM call for agent: %s", author)

        try:
            # Step 1: Validate the request
            try:
                self.model.validate_request(request)
            except ValueError as ex:
                logger.error("Request validation failed for agent %s: %s", author, ex, exc_info=True)
                yield self._create_error_event(context, "validation_error", f"Request validation failed: {str(ex)}")
                return
            except Exception as ex:  # pylint: disable=broad-except
                logger.error("Unexpected validation error for agent %s: %s", author, ex, exc_info=True)
                yield self._create_error_event(context, "validation_unexpected_error", f"Validation error: {str(ex)}")
                return

            # Step 2: Call the model and process responses with telemetry tracing.
            with tracer.start_as_current_span('call_llm'):
                event_id = Event.new_id()
                final_llm_response = None
                aggregated_raw_function_calls: list[dict] = []
                aggregated_event_function_calls: list[dict] = []

                def _append_function_calls(target: list[dict], calls: list) -> None:
                    for call in calls or []:
                        # Keep only telemetry-safe fields for trace attributes.
                        target.append({
                            "id": getattr(call, "id", None),
                            "name": getattr(call, "name", None),
                            "args": getattr(call, "args", None),
                        })

                t_start = time.monotonic()
                t_first_token: Optional[float] = None
                metrics_error_type: Optional[str] = None
                try:
                    async for llm_response in self.model.generate_async(request, stream=stream, ctx=context):
                        if t_first_token is None and llm_response.has_content():
                            t_first_token = time.monotonic()
                        # Collect raw model-level function calls from every chunk.
                        raw_calls = []
                        if llm_response.content and llm_response.content.parts:
                            for part in llm_response.content.parts:
                                if part.function_call:
                                    raw_calls.append(part.function_call)
                        _append_function_calls(aggregated_raw_function_calls, raw_calls)

                        # Create Event directly from LlmResponse
                        event = self._create_event_from_response(context, event_id, llm_response)

                        # Process response with planner if available
                        event = self._process_planning_response(event, context)
                        _append_function_calls(aggregated_event_function_calls, event.get_function_calls())

                        # Create Event directly from LlmResponse
                        event = self._create_event_from_response(context, event_id, llm_response)

                        # Process response with planner if available
                        event = self._process_planning_response(event, context)

                        # Track the latest non-partial response for tracing
                        # In streaming mode, only the final (non-partial) response
                        # contains complete data suitable for telemetry reporting.
                        if not llm_response.partial:
                            final_llm_response = llm_response

                        yield event
                except Exception as ex:
                    metrics_error_type = type(ex).__name__
                    raise
                finally:
                    duration_s = time.monotonic() - t_start
                    ttft_s = (t_first_token - t_start) if t_first_token is not None else duration_s
                    report_call_llm(
                        context,
                        request,
                        final_llm_response,
                        duration_s=duration_s,
                        ttft_s=ttft_s,
                        is_stream=stream,
                        error_type=metrics_error_type,
                    )

                # Trace the LLM call once after the stream completes,
                # using the final complete response to avoid attribute
                # overwrites from multiple partial trace_call_llm calls.
                if final_llm_response is not None:
                    instruction_metadata = getattr(context.agent.instruction, 'metadata', None)
                    trace_call_llm(context,
                                   event_id,
                                   request,
                                   final_llm_response,
                                   instruction_metadata=instruction_metadata,
                                   stream_function_calls_raw=aggregated_raw_function_calls,
                                   stream_function_calls_post_planner=aggregated_event_function_calls)

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("LLM call failed for agent %s: %s", author, ex)
            yield self._create_error_event(context, "llm_call_error", f"LLM call failed: {str(ex)}")

        logger.debug("LLM call completed for agent: %s", author)

    def _create_event_from_response(self, ctx: InvocationContext, event_id: str, response: LlmResponse) -> Event:
        """Create an Event directly from LlmResponse.

        Since Event inherits from LlmResponse, we can directly pass the LlmResponse fields
        to the Event constructor along with the additional Event-specific fields.

        Args:
            ctx: The invocation context containing author, invocation_id, and branch
            event_id: The event ID
            response: The LlmResponse to convert

        Returns:
            Event: The created event
        """
        # Create Event directly with all LlmResponse fields plus Event-specific fields
        return Event(
            # LlmResponse fields
            content=response.content,
            grounding_metadata=response.grounding_metadata,
            partial=response.partial,
            turn_complete=response.turn_complete,
            error_code=response.error_code,
            error_message=response.error_message,
            interrupted=response.interrupted,
            custom_metadata=response.custom_metadata,
            usage_metadata=response.usage_metadata,
            response_id=response.response_id,
            # Event-specific fields extracted from context
            id=event_id,
            invocation_id=ctx.invocation_id,
            author=ctx.agent.name,
            branch=ctx.branch,
        )

    def _create_error_event(self, ctx: InvocationContext, error_code: str, error_message: str) -> Event:
        """Create an error Event.

        Args:
            ctx: The invocation context
            error_code: The error code
            error_message: The error message

        Returns:
            Event: The error event
        """
        return Event(
            invocation_id=ctx.invocation_id,
            author=ctx.agent.name,
            error_code=error_code,
            error_message=error_message,
            branch=ctx.branch,
        )

    def _process_planning_response(self, event: Event, context: InvocationContext) -> Event:
        """Process the event using planner if available.

        Args:
            event: The event to process
            context: The invocation context

        Returns:
            The processed event (modified if planner processing occurred)
        """
        try:
            # Check if agent has planner
            agent = context.agent
            if not hasattr(agent, 'planner') or not agent.planner:
                return event

            # Only process events with content
            if not event.content or not event.content.parts:
                return event

            # Process response parts using planner with event for streaming support
            processed_parts = default_planning_processor.process_response(event.content.parts, agent, context, event)

            if processed_parts:
                # Update the event with processed parts
                event.content.parts = processed_parts
                logger.debug("Processed event content with planner for agent: %s", agent.name)

            return event

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error processing planning response for agent %s: %s", context.agent.name, ex)
            # Return original event on error to avoid breaking the flow
            return event
