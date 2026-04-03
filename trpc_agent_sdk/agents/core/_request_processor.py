# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Request Processor implementation for TRPC Agent framework.

This module provides the RequestProcessor class which handles building LlmRequest
objects from agent configuration and invocation context. It centralizes all the
request building logic that was previously scattered in BaseAgent.

The RequestProcessor is responsible for:
- Creating base LlmRequest objects
- Adding instructions (global + agent-specific)
- Adding tool declarations via ToolsProcessor
- Adding conversation history from session
- Adding current user content
- Handling tool response follow-up requests

"""

from __future__ import annotations

import copy
import inspect
import re
from typing import List
from typing import Optional

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.planners import default_planning_processor
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools import transfer_to_agent
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Part

from .._base_agent import BaseAgent
from ._history_processor import BranchFilterMode
from ._history_processor import HistoryProcessor
from ._history_processor import TimelineFilterMode
from ._skill_processor import SkillsRequestProcessor
from ._tools_processor import ToolsProcessor


class RequestProcessor:
    """Processor for building LlmRequest objects from agent configuration.

    This class centralizes all request building logic and provides a clean
    interface for constructing LlmRequests from agent configuration and
    invocation context. Instead of raising exceptions, it returns Events
    to maintain consistency with the framework's event-driven architecture.
    """

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

    async def build_request(
        self,
        request: LlmRequest,
        agent: BaseAgent,
        ctx: InvocationContext,
        override_messages: Optional[List[Content]] = None,
    ) -> Event:
        """Build a model request from the agent configuration and context.

        This method orchestrates all the request building steps:
        1. Sets generate content config on the request
        2. Adds instructions (global + agent-specific)
        3. Adds tool declarations if tools are available
        4. Adds agent transfer capabilities if needed
        5. Adds conversation history (includes current user message in correct order)
        6. Processes planning if planner is available

        Note: Model name should be set by the caller (e.g., LlmAgent) before calling this method.

        Args:
            request: The LlmRequest object to populate (model name should be already set)
            agent: The BaseAgent to build the request for
            ctx: The invocation context
            override_messages: If provided, use these messages instead of
                              building from session history. Used by TeamAgent
                              to control member agent context.

        Returns:
            Event: Success event if request building succeeds, error event if it fails
        """
        # 1. Set generate content config on the request
        error_event = self._set_generate_content_config(request, agent, ctx)
        if error_event:
            return error_event

        # 2. Add instructions (global + agent-specific)
        error_event = await self._add_instructions_to_request(agent, ctx, request)
        if error_event:
            return error_event

        # 3. Add tool declarations if available
        error_event = await self._add_tools_to_request(agent, ctx, request)
        if error_event:
            return error_event

        # 4. Add agent transfer capabilities if needed
        error_event = await self._add_agent_transfer_capabilities(agent, ctx, request)
        if error_event:
            return error_event

        # 5. Add skills to the request
        error_event = await self._add_skills_to_request(agent, ctx, request)
        if error_event:
            return error_event

        # 6. Add conversation history (includes current user message in correct order)
        if override_messages is not None:
            # Use provided messages directly (for TeamAgent member control)
            for content in override_messages:
                request.contents.append(content)
            logger.debug("Used %s override messages for agent: %s", len(override_messages), agent.name)
        elif agent.include_contents == 'default':
            max_history_messages = getattr(agent, 'max_history_messages', 0)
            timeline_filter_mode = getattr(agent, 'message_timeline_filter_mode', TimelineFilterMode.ALL)
            branch_filter_mode = agent._get_effective_branch_filter_mode()

            error_event = await self._add_conversation_history(
                agent,
                ctx,
                request,
                max_history_messages,
                timeline_filter_mode,
                branch_filter_mode,
            )
            if error_event:
                return error_event

        # 7. Process planning if planner is available
        error_event = await self._add_planning_capabilities(agent, ctx, request)
        if error_event:
            return error_event

        # 8. Process output schema if needed (when tools are also present)
        error_event = await self._add_output_schema_capabilities(agent, ctx, request)
        if error_event:
            return error_event

        logger.debug("Built model request for agent: %s, request: %s", agent.name, request)

    def _set_generate_content_config(self, request: LlmRequest, agent: BaseAgent,
                                     ctx: InvocationContext) -> Optional[Event]:
        """Set the generate content config on the request.

        Args:
            request: The model request to update
            agent: The BaseAgent to get config from
            ctx: The invocation context

        Returns:
            Event: Error event if config setting fails, None if successful
        """
        try:
            # Set config similar to adk-python basic.py
            request.config = (agent.generate_content_config.model_copy(
                deep=True) if agent.generate_content_config else GenerateContentConfig())

            # If agent has output_schema, set it on the config
            if agent.output_schema and hasattr(agent, 'tools') and not agent.tools:
                # Set output schema on the llm request's parameter. It needs the model support.
                request.set_output_schema(agent.output_schema)

            return None  # Success

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error setting generate content config for agent %s: %s", agent.name, ex)
            return self._create_error_event(ctx, "config_error", f"Failed to set generate content config: {str(ex)}")

    async def _add_instructions_to_request(self, agent: BaseAgent, ctx: InvocationContext,
                                           request: LlmRequest) -> Optional[Event]:
        """Add global and agent instructions to the model request.

        Args:
            agent: The BaseAgent to get instructions from
            ctx: The invocation context
            request: The model request to populate

        Returns:
            Event: Error event if instruction resolution fails, None if successful
        """
        instructions_parts = []

        # Add global instruction from root agent (if exists)
        root_agent = agent.root_agent
        if root_agent and root_agent.global_instruction:
            try:
                global_instruction = await self._resolve_global_instruction(root_agent, ctx)
                if global_instruction:
                    instructions_parts.append(global_instruction)
                    logger.debug("Added global instruction from root agent: %s", root_agent.name)
            except Exception as ex:  # pylint: disable=broad-except
                logger.error("Error resolving global instruction: %s", ex)
                return self._create_error_event(ctx, "global_instruction_error",
                                                f"Failed to resolve global instruction: {str(ex)}")

        # Add agent-specific instruction
        if agent.instruction:
            try:
                # Only add agent name to instruction if add_name_to_instruction is True
                if getattr(agent, 'add_name_to_instruction', True):
                    instructions_parts.append(f"You are an agent who's name is [{agent.name}].")
                agent_instruction = await self._resolve_instruction(agent, ctx)
                if agent_instruction:
                    instructions_parts.append(agent_instruction)
                    logger.debug("Added agent instruction for: %s", agent.name)
            except Exception as ex:  # pylint: disable=broad-except
                logger.error("Error resolving agent instruction: %s", ex)
                return self._create_error_event(ctx, "agent_instruction_error",
                                                f"Failed to resolve agent instruction: {str(ex)}")

        # Add code executor instruction if code_executor is enabled
        if agent.code_executor:
            code_executor_instruction = (
                "# NOTICE\n"
                "YOU SHOULD NOT GENERATE CODE EXECUTION RESULT WHICH ARE PREFIXED WITH "
                "CODE EXECUTION RESULT(DON'T SHOW THIS TEXT): \n"
                "YOU SHOULD NOT EXPLAIN THE PROCESS OF GENERATED CODE.\n"
                "YOU CAN SUMMARIZE THE CODE EXECUTION RESULT WHEN YOU GOT IT, "
                "BUT ONLY NEED TO MAKE IT SIMPLE AND CLEAR.\n"
                "THE OUTPUT OF CODE EXECUTION RESULT SHOULD BE PRINTED OUT IN THE LAST LINE.\n\n")
            instructions_parts.append(code_executor_instruction)
            logger.debug("Added code executor instruction for agent: %s", agent.name)

        summary_text = await ctx.session_service.get_session_summary(ctx.session)
        if summary_text:
            instructions_parts.append(f"Here is a brief summary of your previous interactions: {summary_text}")
            logger.debug("Added session summary to request: %s", summary_text)

        # Build and set system prompt if we have instructions
        if instructions_parts:
            try:
                # Combine instructions with newlines
                combined_instructions = "\n\n".join(instructions_parts)

                # Set system prompt using append_instructions
                request.append_instructions([combined_instructions])

                logger.debug("Set system prompt with %s instruction parts", len(instructions_parts))
            except Exception as ex:  # pylint: disable=broad-except
                logger.error("Error setting system prompt: %s", ex)
                return self._create_error_event(ctx, "system_prompt_error", f"Failed to set system prompt: {str(ex)}")

        return None  # Success

    async def _add_tools_to_request(self, agent: BaseAgent, ctx: InvocationContext,
                                    request: LlmRequest) -> Optional[Event]:
        """Add tool declarations to the model request.

        Args:
            agent: The BaseAgent to get tools from
            ctx: The invocation context
            request: The model request to populate

        Returns:
            Event: Error event if tool processing fails, None if successful
        """
        # Prepare tools list - start with agent's existing tools
        tools_to_process = agent.tools.copy() if agent.tools else []

        # Add transfer tool if agent transfer should be enabled
        if agent._should_enable_agent_transfer():
            try:
                transfer_tool = FunctionTool(transfer_to_agent)
                tools_to_process.append(transfer_tool)
                logger.debug("Added transfer_to_agent tool for agent: %s", agent.name)
            except Exception as ex:  # pylint: disable=broad-except
                logger.error("Error adding transfer tool for agent %s: %s", agent.name, ex)
                return self._create_error_event(ctx, "transfer_tool_error", f"Failed to add transfer tool: {str(ex)}")

        # Process all tools if we have any
        if tools_to_process:
            try:
                tools_processor = ToolsProcessor(tools_to_process)
                await tools_processor.process_llm_request(ctx, request)
                logger.debug("Processed %s tools for agent: %s", len(tools_to_process), agent.name)
            except Exception as ex:  # pylint: disable=broad-except
                logger.error("Error processing tools for agent %s: %s", agent.name, ex)
                return self._create_error_event(ctx, "tool_processing_error", f"Failed to process tools: {str(ex)}")

        return None  # Success

    async def _add_skills_to_request(self, agent: BaseAgent, ctx: InvocationContext,
                                     request: LlmRequest) -> Optional[Event]:
        """Add skills to the model request.

        Args:
            agent: The BaseAgent to get skills from
            ctx: The invocation context
            request: The model request to populate

        Returns:
            Event: Error event if skill processing fails, None if successful
        """
        skill_repository = getattr(agent, 'skill_repository', None)
        if skill_repository:
            try:
                skills_processor = SkillsRequestProcessor(skill_repository)
                skill_names = await skills_processor.process_llm_request(ctx, request)
                logger.debug("Processed %s skills for agent: %s", len(skill_names), agent.name)
                return None  # Success
            except Exception as ex:  # pylint: disable=broad-except
                logger.error("Error processing skills for agent %s: %s", agent.name, ex)
                return self._create_error_event(ctx, "skill_processing_error", f"Failed to process skills: {str(ex)}")

    async def _add_agent_transfer_capabilities(self, agent: BaseAgent, ctx: InvocationContext,
                                               request: LlmRequest) -> Optional[Event]:
        """Add agent transfer capabilities to the model request if needed.

        Args:
            agent: The BaseAgent to check for transfer capabilities
            ctx: The invocation context
            request: The model request to populate

        Returns:
            Event: Error event if transfer processing fails, None if successful
        """
        # Only add transfer capabilities if the agent should support transfers
        if agent._should_enable_agent_transfer():
            try:
                from ._agent_transfer_processor import default_agent_transfer_processor

                error_event = await default_agent_transfer_processor.process_agent_transfer(request, agent, ctx)
                if error_event:
                    return error_event
                logger.debug("Added agent transfer capabilities for agent: %s", agent.name)
            except Exception as ex:  # pylint: disable=broad-except
                logger.error("Error adding agent transfer capabilities for agent %s: %s", agent.name, ex)
                return self._create_error_event(ctx, "agent_transfer_setup_error",
                                                f"Failed to add agent transfer capabilities: {str(ex)}")

        return None  # Success

    async def _add_conversation_history(self,
                                        agent: BaseAgent,
                                        ctx: InvocationContext,
                                        request: LlmRequest,
                                        max_history_messages: int = 0,
                                        timeline_filter_mode: TimelineFilterMode = TimelineFilterMode.ALL,
                                        branch_filter_mode: BranchFilterMode = BranchFilterMode.ALL) -> Optional[Event]:
        """Add conversation history to the model request.

        This method retrieves conversation history from the session and adds
        it to the model request, properly handling tool calls and tool results
        to enable multi-turn conversations with tools.

        Args:
            agent: The BaseAgent to get history for
            ctx: The invocation context
            request: The model request to populate
            max_history_messages: Maximum number of history messages (0 = no limit)
            timeline_filter_mode: Timeline filter mode enum
            branch_filter_mode: Branch filter mode enum

        Returns:
            Event: Error event if history retrieval fails, None if successful
        """
        try:
            if not ctx.session or not ctx.session.events:
                logger.debug("No conversation history to add for agent: %s", agent.name)
                return None

            logger.debug("Processing %s events for conversation history", len(ctx.session.events))

            # Get conversation contents with filtering using HistoryProcessor
            history_processor = HistoryProcessor(
                max_history_messages=max_history_messages,
                timeline_filter_mode=timeline_filter_mode,
                branch_filter_mode=branch_filter_mode,
            )
            filtered_events = history_processor.filter_events(
                ctx=ctx,
                events=ctx.session.events,
            )

            # Process foreign agent events for conversion to user context
            processed_events = []
            for event in filtered_events:
                # If this is a foreign agent event (from another branch), convert it to user context
                if self._is_other_agent_reply(ctx.branch, event):
                    event = self._convert_foreign_event(event, agent)
                processed_events.append(event)

            # Rearrange events for proper function call/response pairing
            processed_events = self._rearrange_events_for_async_function_responses_in_history(processed_events)

            # Merge consecutive same-role contents to avoid redundant conversation turns
            merged_contents = self._merge_consecutive_same_role_contents(processed_events)

            # Add contents to request as conversation messages
            for content in merged_contents:
                self._add_content_to_request(request, content, agent)

            logger.debug("Added %s contents (merged from %s) to conversation history for agent: %s",
                         len(merged_contents), len(processed_events), agent.name)
            return None  # Success

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error adding conversation history for agent %s: %s", agent.name, ex)
            return self._create_error_event(ctx, "history_error", f"Failed to add conversation history: {str(ex)}")

    def _merge_consecutive_same_role_contents(self, events: List[Event]) -> List[Event]:
        """Merge consecutive events with the same content role.

        This method combines consecutive Content objects that have the same role
        to avoid redundant conversation turns in the LLM request.

        IMPORTANT: User messages from different invocation_ids (conversation turns)
        are NOT merged to preserve turn boundaries.

        Args:
            events: List of events to merge

        Returns:
            List of events with consecutive same-role contents merged
        """
        if not events:
            return events

        merged_events = []
        current_event = None

        for event in events:
            if not event.content or not event.content.parts:
                # Event without content - add as is
                if current_event:
                    merged_events.append(current_event)
                    current_event = None
                merged_events.append(event)
                continue

            # Determine the effective role for this event
            event_role = self._get_effective_content_role(event)

            if current_event is None:
                # First event with content
                current_event = copy.deepcopy(event)
                # Ensure role is set correctly
                if current_event.content:
                    current_event.content.role = event_role
            else:
                # Check if we can merge with previous event
                current_role = self._get_effective_content_role(current_event)

                if current_role == event_role:
                    # Same role - merge the parts
                    if current_event.content and event.content:
                        # Add all parts from the new event to the current event
                        current_event.content.parts.extend(event.content.parts)
                else:
                    # Different role - finalize current and start new
                    merged_events.append(current_event)
                    current_event = copy.deepcopy(event)
                    # Ensure role is set correctly
                    if current_event.content:
                        current_event.content.role = event_role

        # Don't forget the last event
        if current_event:
            merged_events.append(current_event)

        return merged_events

    def _get_effective_content_role(self, event: Event) -> Optional[str]:
        """Get the effective content role for an event.

        This method determines what role the event's content should have,
        considering both explicit roles and fallback logic.

        Args:
            event: The event to get the role for

        Returns:
            The effective role for the event's content
        """
        if not event.content:
            return None

        # If role is already set, use it
        if event.content.role:
            return event.content.role

        # Determine role based on author and content type
        if event.author == "user":
            return "user"
        else:
            # For agent responses, check if this contains function responses or code execution results
            if event.content.parts:
                has_function_response = any(part.function_response for part in event.content.parts)
                has_code_execution_result = any(part.code_execution_result for part in event.content.parts)
                if has_function_response or has_code_execution_result:
                    # Function responses and code execution results should be presented as user content to the LLM
                    # This ensures proper role alternation (user/assistant) in multi-turn conversations
                    return "user"
            # Regular agent text responses should be model role
            return "model"

    def _convert_foreign_event(self, event: Event, agent: BaseAgent) -> Event:
        """Converts an event authored by another agent as a user-content event.

        This provides another agent's output as context to the current agent.

        Note: Events containing transfer_to_agent are filtered out earlier in
        HistoryProcessor, so we don't need to check for them here.

        Args:
            event: The event to convert
            agent: The current agent (to check add_name_to_instruction setting)

        Returns:
            Converted event with agent name prefix added/removed based on configuration
        """
        if not event.content or not event.content.parts:
            return event

        # Check if we should include agent names in the converted content
        include_agent_name = getattr(agent, 'add_name_to_instruction', True)

        # Create new content with user role
        content_parts = []

        # First, extract the meaningful content from the event
        text_content = []
        tool_calls = []
        tool_responses = []

        for part in event.content.parts:
            if part.text:
                text_content.append(part.text)
            elif part.function_call:
                tool_calls.append(part.function_call)
            elif part.function_response:
                tool_responses.append(part.function_response)

        # Format the content appropriately for user context
        if text_content:
            # For chain agents, the text content from previous agent is the main result
            combined_text = " ".join(text_content).strip()
            if combined_text:
                # Add "[agent] said:" prefix only if add_name_to_instruction is True
                if include_agent_name:
                    content_parts.append(Part.from_text(text=f"[{event.author}]: {combined_text}\n"))
                else:
                    content_parts.append(Part.from_text(text=f"{combined_text}\n"))

        # Include tool information for context if needed
        for tool_call in tool_calls:
            if include_agent_name:
                content_parts.append(
                    Part.from_text(text=(f"[{event.author}] called tool `{tool_call.name}`"
                                         f" with parameters: {tool_call.args}\n")))
            else:
                content_parts.append(
                    Part.from_text(text=(f"Called tool `{tool_call.name}`"
                                         f" with parameters: {tool_call.args}\n")))

        for tool_response in tool_responses:
            if include_agent_name:
                content_parts.append(
                    Part.from_text(text=(f"[{event.author}] `{tool_response.name}` tool"
                                         f" returned result: {tool_response.response}\n")))
            else:
                content_parts.append(
                    Part.from_text(text=(f"`{tool_response.name}` tool"
                                         f" returned result: {tool_response.response}\n")))

        # Create new content
        new_content = Content(role="user", parts=content_parts)

        # Create new event with converted content
        converted_event = copy.deepcopy(event)
        converted_event.author = "user"
        converted_event.content = new_content

        return converted_event

    def _rearrange_events_for_async_function_responses_in_history(self, events: List[Event]) -> List[Event]:
        """Rearrange the async function_response events in the history."""
        function_call_id_to_response_events_index = {}

        for i, event in enumerate(events):
            function_responses = self._get_function_responses(event)
            if function_responses:
                for function_response in function_responses:
                    if hasattr(function_response, "id"):
                        function_call_id = function_response.id
                        function_call_id_to_response_events_index[function_call_id] = i

        result_events = []
        for event in events:
            if self._get_function_responses(event):
                # function_response should be handled together with function_call below
                continue
            elif self._get_function_calls(event):
                function_response_events_indices = set()
                for function_call in self._get_function_calls(event):
                    if hasattr(function_call, "id"):
                        function_call_id = function_call.id
                        if function_call_id in function_call_id_to_response_events_index:
                            function_response_events_indices.add(
                                function_call_id_to_response_events_index[function_call_id])

                result_events.append(event)
                if not function_response_events_indices:
                    continue

                if len(function_response_events_indices) == 1:
                    result_events.append(events[next(iter(function_response_events_indices))])
                else:  # Merge all async function_response as one response event
                    result_events.append(
                        self._merge_function_response_events(
                            [events[i] for i in sorted(function_response_events_indices)]))
                continue
            else:
                result_events.append(event)

        return result_events

    def _merge_function_response_events(self, function_response_events: List[Event]) -> Event:
        """Merges a list of function_response events into one event."""
        if not function_response_events:
            raise ValueError("At least one function_response event is required.")

        merged_event = copy.deepcopy(function_response_events[0])
        parts_in_merged_event = merged_event.content.parts

        if not parts_in_merged_event:
            raise ValueError("There should be at least one function_response part.")

        part_indices_in_merged_event = {}
        for idx, part in enumerate(parts_in_merged_event):
            if hasattr(part, "function_response") and part.function_response:
                if hasattr(part.function_response, "id"):
                    function_call_id = part.function_response.id
                    part_indices_in_merged_event[function_call_id] = idx

        for event in function_response_events[1:]:
            if not event.content.parts:
                continue

            for part in event.content.parts:
                if hasattr(part, "function_response") and part.function_response:
                    if hasattr(part.function_response, "id"):
                        function_call_id = part.function_response.id
                        if function_call_id in part_indices_in_merged_event:
                            parts_in_merged_event[part_indices_in_merged_event[function_call_id]] = part
                        else:
                            parts_in_merged_event.append(part)
                            part_indices_in_merged_event[function_call_id] = len(parts_in_merged_event) - 1
                else:
                    parts_in_merged_event.append(part)

        return merged_event

    def _get_function_calls(self, event: Event) -> List:
        """Extract function calls from an event."""
        function_calls = []

        # Check for function_call in content parts - this is where tool execution results are now stored
        if event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    # Skip transfer_to_agent function calls
                    if part.function_call.name == "transfer_to_agent":
                        continue
                    function_calls.append(part.function_call)

        return function_calls

    def _get_function_responses(self, event: Event) -> List:
        """Extract function responses from an event."""
        function_responses = []
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.function_response:
                    # Skip transfer_to_agent function responses
                    if part.function_response.name == "transfer_to_agent":
                        continue
                    function_responses.append(part.function_response)
        return function_responses

    def _add_content_to_request(self, request: LlmRequest, event: Event, agent: Optional['BaseAgent'] = None) -> None:
        """Add a content event to the model request as appropriate Content type."""
        if not event.content or not event.content.parts:
            return

        # Check if agent has a planner - if so, don't filter thought content
        should_filter_thoughts = True
        if agent and hasattr(agent, 'planner') and agent.planner:
            should_filter_thoughts = False
            logger.debug("Agent %s has planner, keeping thought content", agent.name)

        # Filter out parts where thought=True (only if agent doesn't have planner)
        filtered_parts = []
        for part in event.content.parts:
            if should_filter_thoughts and hasattr(part, 'thought') and part.thought is True:
                # Skip parts marked as thoughts
                logger.debug("Skipping thought content: %s...", part.text[:100] if part.text else 'non-text part')
                continue
            filtered_parts.append(part)

        # If no parts remain after filtering, don't add this content
        if not filtered_parts:
            logger.debug("All parts were filtered out as thoughts, skipping content")
            return

        # The event.content is already a Content object, so we can add it directly
        # But we need to ensure the role is set correctly based on event type
        content = event.content
        content.parts = filtered_parts

        # Check if role is already explicitly set (e.g., by _convert_foreign_event)
        # If so, respect that role and don't override it
        if content.role:
            # Role already set, use it as is
            pass
        else:
            content.role = "user"

        # Add the content to the request
        request.contents.append(content)

    async def _resolve_instruction(self, agent: BaseAgent, ctx: InvocationContext) -> str:
        """Resolve the instruction for the given agent.

        Args:
            agent: The BaseAgent to resolve instruction for
            ctx: The invocation context

        Returns:
            str: The resolved instruction with template substitution applied

        Raises:
            Exception: If instruction resolution fails (caught by caller)
        """
        if isinstance(agent.instruction, str):
            instruction = agent.instruction
        else:
            instruction = agent.instruction(ctx)  # type: ignore
            if inspect.isawaitable(instruction):
                instruction = await instruction

        # Apply template substitution using session state
        return self._apply_template_substitution(instruction, ctx)

    async def _resolve_global_instruction(self, root_agent: BaseAgent, ctx: InvocationContext) -> str:
        """Resolve the global instruction from the root agent.

        Args:
            root_agent: The root agent to get global instruction from
            ctx: The invocation context

        Returns:
            str: The resolved global instruction with template substitution applied

        Raises:
            Exception: If global instruction resolution fails (caught by caller)
        """
        if root_agent.global_instruction:
            if isinstance(root_agent.global_instruction, str):
                instruction = root_agent.global_instruction
            else:
                instruction = root_agent.global_instruction(ctx)  # type: ignore
                if inspect.isawaitable(instruction):
                    instruction = await instruction

            # Apply template substitution using session state
            return self._apply_template_substitution(instruction, ctx)
        return ""

    async def _add_planning_capabilities(self, agent: BaseAgent, ctx: InvocationContext,
                                         request: LlmRequest) -> Optional[Event]:
        """Add planning capabilities to the model request if planner is available.

        Args:
            agent: The BaseAgent to check for planner
            ctx: The invocation context
            request: The model request to populate

        Returns:
            Event: Error event if planning processing fails, None if successful
        """
        # Only process planning if agent has a planner
        if not hasattr(agent, 'planner') or not agent.planner:
            return None

        try:

            error_event = default_planning_processor.process_request(request, agent, ctx)
            if error_event:
                return error_event
            logger.debug("Added planning capabilities for agent: %s", agent.name)
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error adding planning capabilities for agent %s: %s", agent.name, ex)
            return self._create_error_event(ctx, "planning_setup_error",
                                            f"Failed to add planning capabilities: {str(ex)}")

        return None  # Success

    async def _add_output_schema_capabilities(self, agent: BaseAgent, ctx: InvocationContext,
                                              request: LlmRequest) -> Optional[Event]:
        """Add output schema capabilities to the model request if needed.

        Args:
            agent: The BaseAgent to check for output schema capabilities
            ctx: The invocation context
            request: The model request to populate

        Returns:
            Event: Error event if output schema processing fails, None if successful
        """
        # Only add output schema capabilities if the agent has both output_schema and tools
        if hasattr(agent, "output_schema") and agent.output_schema and agent.tools:
            try:
                from ._output_schema_processor import default_output_schema_processor

                await default_output_schema_processor.run_async(ctx, request)
                logger.debug("Added output schema capabilities for agent: %s", agent.name)
            except Exception as ex:  # pylint: disable=broad-except
                logger.error("Error adding output schema capabilities for agent %s: %s", agent.name, ex)
                return self._create_error_event(ctx, "output_schema_setup_error",
                                                f"Failed to add output schema capabilities: {str(ex)}")

        return None  # Success

    def _is_other_agent_reply(
        self,
        current_branch: Optional[str],
        event: Event,
    ) -> bool:
        """Determine if an event is a reply from another agent.

        Args:
            current_branch: The current agent's branch
            event: The event to check

        Returns:
            True if the event is from a foreign agent and should be converted
            to user context, False otherwise
        """
        # User messages are never considered "other agent"
        if event.author == "user":
            return False

        # If no branch information, consider it "other agent" by default
        if not current_branch or not event.branch:
            return True

        # Return True if event is from a different branch (other agent)
        return event.branch != current_branch

    def _apply_template_substitution(self, instruction: str, ctx: InvocationContext) -> str:
        """Apply template substitution to replace {key} placeholders with state values.

        This method replaces template placeholders like {user:theme}, {app:config},
        {session_key}, etc. with actual values from the session state.

        This implementation is inspired by adk-python's inject_session_state but
        adapted for trpc_agent_sdk's architecture.

        Args:
            instruction: The instruction string containing template placeholders
            ctx: The invocation context with session state

        Returns:
            str: The instruction with template placeholders replaced with actual values
        """
        if not instruction or '{' not in instruction:
            return instruction

        # Get all state values from the session
        state_dict = ctx.session.state if ctx.session else {}

        try:
            # Use regex to find and replace placeholders one by one
            def replace_placeholder(match):
                """Replace a single placeholder with its value."""
                var_name = match.group().lstrip('{').rstrip('}').strip()
                optional = False

                # Handle optional variables (ending with ?)
                if var_name.endswith('?'):
                    optional = True
                    var_name = var_name.removesuffix('?')

                # Check if the variable exists in state
                if var_name in state_dict:
                    # Convert the value to string safely
                    value = state_dict[var_name]
                    return str(value) if value is not None else ""
                else:
                    if optional:
                        # Optional variable not found - return empty string
                        return ""
                    else:
                        # Required variable not found - leave placeholder unchanged
                        # This follows the behavior of the original SafeFormatter approach
                        return match.group()

            # Use regex pattern similar to adk-python but simpler for trpc_agent_sdk
            # This matches {variable_name} patterns including optional ones with ?
            pattern = r'\{[^{}]*\}'
            result = re.sub(pattern, replace_placeholder, instruction)

            logger.debug("Template substitution completed. Original: %s..., Result: %s...", instruction[:100],
                         result[:100])
            return result
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Template substitution failed for instruction: %s", ex)
            # Return original instruction if formatting fails
            return instruction


# Create a default instance for convenience
default_request_processor = RequestProcessor()
