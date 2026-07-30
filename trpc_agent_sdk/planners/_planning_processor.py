# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Planning Processor module for TRPC Agent framework.

This module provides the PlanningProcessor class which integrates planners
into the LLM request/response flow, handling planning instructions and
response processing.
"""

from __future__ import annotations

from typing import List
from typing import Optional

from trpc_agent_sdk.abc import AgentABC
from trpc_agent_sdk.abc import PlannerABC as BasePlanner
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.types import Part


class PlanningProcessor:
    """Processor for planning-related request and response handling.

    This class integrates planners into the LLM workflow by:
    1. Adding planning instructions to requests
    2. Processing responses to filter planning content
    3. Managing thought removal from requests
    """

    def process_request(self, llm_request: LlmRequest, agent: AgentABC, context: InvocationContext) -> Optional[Event]:
        """Process the LLM request to add planning capabilities.

        Args:
            llm_request: The LLM request to process
            agent: The LlmAgent using the planner
            context: The invocation context

        Returns:
            Error event if processing fails, None if successful
        """
        try:
            planner = self._get_planner(agent)
            if not planner:
                return None

            # Apply built-in thinking config if applicable
            from ._built_in_planner import BuiltInPlanner

            if isinstance(planner, BuiltInPlanner):
                planner.apply_thinking_config(llm_request)

            # Add planning instructions
            planning_instruction = planner.build_planning_instruction(context, llm_request)
            if planning_instruction:
                llm_request.append_instructions([planning_instruction])
                logger.debug("Added planning instruction for agent: %s", agent.name)

            # Remove thought content from request
            self._remove_thought_from_request(llm_request)

            return None  # Success

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error processing planning request for agent %s: %s", agent.name, ex)
            return self._create_error_event(context, "planning_request_error",
                                            f"Failed to process planning request: {str(ex)}")

    def process_response(self,
                         response_parts: List[Part],
                         agent: AgentABC,
                         context: InvocationContext,
                         event: Optional[Event] = None) -> Optional[List[Part]]:
        """Process the LLM response using the planner.

        Args:
            response_parts: The LLM response parts to process
            agent: The LlmAgent using the planner
            context: The invocation context
            event: The optional event containing partial flag for streaming support

        Returns:
            Processed response parts, or None if no processing occurred
        """
        try:
            if not response_parts:
                return None

            planner = self._get_planner(agent)
            if not planner:
                return None

            # Get partial flag from event if available
            is_partial = event.partial if event else False

            # Process the response using the planner with streaming support
            processed_parts = planner.process_planning_response(context, response_parts, is_partial)

            if processed_parts:
                logger.debug("Processed %s response parts with planner for agent: %s", len(processed_parts), agent.name)
                return processed_parts

            return None  # No processing needed

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error processing planning response for agent %s: %s", agent.name, ex)
            # Return original parts on error to avoid breaking the flow
            return response_parts

    def _get_planner(self, agent: AgentABC) -> Optional["BasePlanner"]:
        """Get the planner from the agent if available.

        Args:
            agent: The LlmAgent to get planner from

        Returns:
            The planner instance or None if not available
        """
        if not hasattr(agent, "planner") or not agent.planner:
            return None

        if isinstance(agent.planner, BasePlanner):
            return agent.planner

        # Fallback to PlanReActPlanner if planner is not a BasePlanner
        from ._plan_re_act_planner import PlanReActPlanner

        logger.warning("Agent %s planner is not a BasePlanner, using PlanReActPlanner", agent.name)
        return PlanReActPlanner()

    def _remove_thought_from_request(self, llm_request: LlmRequest) -> None:
        """Remove thought flag from the request contents.

        Some planners mark content as thoughts which should not be sent to the LLM
        in subsequent requests. The thought content commonly should be removed
        which cause the context not completely. So remove this flag to
        force pass the thought part to LLM.

        Args:
            llm_request: The LLM request to process
        """
        if not llm_request.contents:
            return

        for content in llm_request.contents:
            if not content.parts:
                continue
            for part in content.parts:
                part.thought = None

    def _create_error_event(self, ctx: InvocationContext, error_code: str, error_message: str) -> Event:
        """Create an error event with the agent name from context.

        Args:
            ctx: The invocation context containing agent information
            error_code: The error code for the event
            error_message: The error message for the event

        Returns:
            Event: Error event with proper attribution
        """
        return Event(
            invocation_id=ctx.invocation_id,
            author=ctx.agent.name,
            error_code=error_code,
            error_message=error_message,
        )


# Create a default instance for convenience
default_planning_processor = PlanningProcessor()
