# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# Below code are copy and modified from https://github.com/ag-ui-protocol/ag-ui.git
#
# MIT License
#
# Copyright (c) 2025
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
"""Main AgUiAgent implementation for bridging AG-UI Protocol with TRPC Agent."""

import asyncio
import inspect
import json
from typing import Any
from typing import AsyncGenerator
from typing import Awaitable
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional

from ag_ui.core import BaseEvent
from ag_ui.core import EventType
from ag_ui.core import RunAgentInput
from ag_ui.core import RunErrorEvent
from ag_ui.core import RunFinishedEvent
from ag_ui.core import RunStartedEvent
from ag_ui.core import SystemMessage
from ag_ui.core import ToolCallEndEvent
from ag_ui.core import ToolCallResultEvent
from starlette.requests import Request
from trpc_agent_sdk import cancel
from trpc_agent_sdk import types
from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.configs import RunConfig as TRPCRunConfig
from trpc_agent_sdk.events import EventTranslatorBase
from trpc_agent_sdk.events import LongRunningEvent
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.memory import BaseMemoryService
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import BaseSessionService
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import LongRunningFunctionTool
from trpc_agent_sdk.types import Content

from ._client_proxy_toolset import ClientProxyToolset
from ._converters import convert_message_content_to_parts
from ._event_translator import EventTranslator
from ._execution_state import ExecutionState
from ._feed_back_content import AgUiUserFeedBack
from ._http_req import set_agui_http_req
from ._session_manager import SessionManager


class AgUiAgent:
    """Middleware to bridge AG-UI Protocol with TRPC agents.

    This agent translates between the AG-UI protocol events and TRPC agent events,
    managing sessions, state, and the lifecycle of TRPC agents.
    """

    def __init__(
        self,
        # TRPC Agent instance
        trpc_agent: BaseAgent,
        *,
        # App identification
        app_name: Optional[str] = None,
        app_name_extractor: Optional[Callable[[RunAgentInput], str]] = None,
        # User identification
        user_id: Optional[str] = None,
        # user_id_extractor: Function to extract user ID dynamically from input
        user_id_extractor: Optional[Callable[[RunAgentInput], str]] = None,
        # Storage ServicesD
        session_service: Optional[BaseSessionService] = None,
        # Memory service
        memory_service: Optional[BaseMemoryService] = None,
        # Configuration
        run_config_factory: Optional[Callable[[RunAgentInput], TRPCRunConfig]] = None,
        # Service configuration
        use_in_memory_services: bool = True,
        # Tool configuration
        execution_timeout_seconds: int = 600,  # 10 minutes
        tool_timeout_seconds: int = 300,  # 5 minutes
        max_concurrent_executions: int = 10,
        # Custom user feedback handler
        user_feedback_handler: Optional[Callable[[AgUiUserFeedBack], Awaitable[None]]] = None,
        # Session cleanup configuration
        session_timeout_seconds: Optional[int] = 1200,
        cleanup_interval_seconds: int = 300,  # 5 minutes default
        # Session management
        max_sessions_per_user: Optional[int] = None,
        # Auto cleanup
        auto_cleanup: bool = True,
        # Cancel wait timeout
        cancel_wait_timeout: float = 3.0,
        # Custom event translator for LangGraph events
        event_translator: Optional[EventTranslatorBase[BaseEvent, Any]] = None,
    ):
        """Initialize the AgUiAgent.

        Args:
            trpc_agent: The TRPC agent instance to use
            app_name: Static application name for all requests
            app_name_extractor: Function to extract app name dynamically from input
            user_id: Static user ID for all requests
            user_id_extractor: Function to extract user ID dynamically from input
            session_service: Session management service (defaults to InMemorySessionService)
            memory_service: Conversation memory and search service (also enables automatic session memory)
            run_config_factory: Function to create RunConfig per request
            use_in_memory_services: Use in-memory implementations for unspecified services
            execution_timeout_seconds: Timeout for entire execution
            tool_timeout_seconds: Timeout for individual tool calls
            max_concurrent_executions: Maximum concurrent background executions
            user_feedback_handler: Optional async callback function to handle user feedback after getting tool results.
                                  Signature: async (content: AgUiUserFeedBack) -> None
                                  Can modify session state via content.session. If session is modified,
                                  it will be updated automatically.
            cancel_wait_timeout: Timeout in seconds for waiting on cancel operation to complete (default: 3.0).
                                 If not configured properly, the cancel operation may not execute successfully,
                                 potentially causing streaming text to not be preserved in the session.
            event_translator: Optional custom event translator for LangGraph events. If provided, events
                             created by LangGraphEventWriter will be processed by this translator instead
                             of the default EventTranslator.
        """
        if app_name and app_name_extractor:
            raise ValueError("Cannot specify both 'app_name' and 'app_name_extractor'")

        # app_name, app_name_extractor, or neither (use agent name as default)

        if user_id and user_id_extractor:
            raise ValueError("Cannot specify both 'user_id' and 'user_id_extractor'")

        self._trpc_agent = trpc_agent
        self._static_app_name = app_name
        self._app_name_extractor = app_name_extractor
        self._static_user_id = user_id
        self._user_id_extractor = user_id_extractor
        self._run_config_factory = run_config_factory or self._default_run_config

        # Initialize services with intelligent defaults
        if use_in_memory_services:
            self._memory_service = memory_service or InMemoryMemoryService()
        else:
            # Require explicit services for production
            self._memory_service = memory_service

        # Session lifecycle management - use singleton
        # Use provided session service or create default based on use_in_memory_services
        if session_service is None:
            session_service = InMemorySessionService()  # Default for both dev and production

        self._session_manager = SessionManager.get_instance(
            session_service=session_service,
            memory_service=self._memory_service,  # Pass memory service for automatic session memory
            session_timeout_seconds=session_timeout_seconds,  # 20 minutes default
            cleanup_interval_seconds=cleanup_interval_seconds,
            max_sessions_per_user=max_sessions_per_user,  # No limit by default
            auto_cleanup=auto_cleanup,  # Enable by default
        )

        # Tool execution tracking
        self._active_executions: Dict[str, ExecutionState] = {}
        self._execution_timeout = execution_timeout_seconds
        self._tool_timeout = tool_timeout_seconds
        self._max_concurrent = max_concurrent_executions
        self._execution_lock = asyncio.Lock()
        self._user_feedback_handler = user_feedback_handler
        self._cancel_wait_timeout = cancel_wait_timeout
        self._custom_event_translator = event_translator

        # Session lookup cache for efficient session ID to metadata mapping
        # Maps session_id -> {"app_name": str, "user_id": str}
        self._session_lookup_cache: Dict[str, Dict[str, str]] = {}

        # Event translator will be created per-session for thread safety

        # Cleanup is managed by the session manager
        # Will start when first async operation runs

    def _get_session_metadata(self, session_id: str) -> Optional[Dict[str, str]]:
        """Get session metadata (app_name, user_id) for a session ID efficiently.

        Args:
            session_id: The session ID to lookup

        Returns:
            Dictionary with app_name and user_id, or None if not found
        """
        # Try cache first for O(1) lookup
        if session_id in self._session_lookup_cache:
            return self._session_lookup_cache[session_id]

        # Fallback to linear search if not in cache (for existing sessions)
        # This maintains backward compatibility
        try:
            for uid, keys in self._session_manager._user_sessions.items():
                for key in keys:
                    if key.endswith(f":{session_id}"):
                        app_name = key.split(":", 1)[0]
                        metadata = {"app_name": app_name, "user_id": uid}
                        # Cache for future lookups
                        self._session_lookup_cache[session_id] = metadata
                        return metadata
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error during session metadata lookup for %s: %s", session_id, ex)

        return None

    def get_app_name(self, input: RunAgentInput) -> str:
        """Resolve app name with clear precedence."""
        if self._static_app_name:
            return self._static_app_name
        elif self._app_name_extractor:
            return self._app_name_extractor(input)
        else:
            return self._default_app_extractor(input)

    def _default_app_extractor(self, input: RunAgentInput) -> str:
        """Default app extraction logic - use agent name directly."""
        # Use the TRPC agent's name as app name
        try:
            return self._trpc_agent.name
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Could not get agent name for app_name, using default: %s", ex)
            return "AG-UI TRPC Agent"

    def get_user_id(self, input: RunAgentInput) -> str:
        """Resolve user ID with clear precedence."""
        if self._static_user_id:
            return self._static_user_id
        elif self._user_id_extractor:
            return self._user_id_extractor(input)
        else:
            return self._default_user_extractor(input)

    def _default_user_extractor(self, input: RunAgentInput) -> str:
        """Default user extraction logic."""
        # Use thread_id as default (assumes thread per user)
        return f"thread_user_{input.thread_id}"

    async def _add_pending_tool_call_with_context(self, session_id: str, tool_call_id: str, app_name: str,
                                                  user_id: str):
        """Add a tool call to the session's pending list for HITL tracking.

        Args:
            session_id: The session ID (thread_id)
            tool_call_id: The tool call ID to track
            app_name: App name (for session lookup)
            user_id: User ID (for session lookup)
        """
        logger.debug("Adding pending tool call %s for session %s, app_name=%s, user_id=%s", tool_call_id, session_id,
                     app_name, user_id)
        try:
            # Get current pending calls using SessionManager
            pending_calls = await self._session_manager.get_state_value(session_id=session_id,
                                                                        app_name=app_name,
                                                                        user_id=user_id,
                                                                        key="pending_tool_calls",
                                                                        default=[])

            # Add new tool call if not already present
            if tool_call_id not in pending_calls:
                pending_calls.append(tool_call_id)

                # Update the state using SessionManager
                success = await self._session_manager.set_state_value(
                    session_id=session_id,
                    app_name=app_name,
                    user_id=user_id,
                    key="pending_tool_calls",
                    value=pending_calls,
                )

                if success:
                    logger.debug("Added tool call %s to session %s pending list", tool_call_id, session_id)
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Failed to add pending tool call %s to session %s: %s", tool_call_id, session_id, ex)

    async def _remove_pending_tool_call(self, session_id: str, tool_call_id: str):
        """Remove a tool call from the session's pending list.

        Uses efficient session lookup to find the session without needing explicit app_name/user_id.

        Args:
            session_id: The session ID (thread_id)
            tool_call_id: The tool call ID to remove
        """
        try:
            # Use efficient session metadata lookup
            metadata = self._get_session_metadata(session_id)

            if metadata:
                app_name = metadata["app_name"]
                user_id = metadata["user_id"]

                # Get current pending calls using SessionManager
                pending_calls = await self._session_manager.get_state_value(session_id=session_id,
                                                                            app_name=app_name,
                                                                            user_id=user_id,
                                                                            key="pending_tool_calls",
                                                                            default=[])

                # Remove tool call if present
                if tool_call_id in pending_calls:
                    pending_calls.remove(tool_call_id)

                    # Update the state using SessionManager
                    success = await self._session_manager.set_state_value(
                        session_id=session_id,
                        app_name=app_name,
                        user_id=user_id,
                        key="pending_tool_calls",
                        value=pending_calls,
                    )

                    if success:
                        logger.debug("Removed tool call %s from session %s pending list", tool_call_id, session_id)
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Failed to remove pending tool call %s from session %s: %s", tool_call_id, session_id, ex)

    async def _has_pending_tool_calls(self, session_id: str) -> bool:
        """Check if session has pending tool calls (HITL scenario).

        Args:
            session_id: The session ID (thread_id)

        Returns:
            True if session has pending tool calls
        """
        try:
            # Use efficient session metadata lookup
            metadata = self._get_session_metadata(session_id)

            if metadata:
                app_name = metadata["app_name"]
                user_id = metadata["user_id"]

                # Get pending calls using SessionManager
                pending_calls = await self._session_manager.get_state_value(session_id=session_id,
                                                                            app_name=app_name,
                                                                            user_id=user_id,
                                                                            key="pending_tool_calls",
                                                                            default=[])
                return len(pending_calls) > 0
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Failed to check pending tool calls for session %s: %s", session_id, ex)

        return False

    def _default_run_config(self, input: RunAgentInput) -> TRPCRunConfig:
        """Create default RunConfig with streaming enabled."""
        return TRPCRunConfig(streaming=True)

    def _extract_long_running_tool_names(self, agent: BaseAgent) -> List[str]:
        """Extract long-running tool names from the TRPC agent.

        Args:
            agent: The TRPC agent to extract tool names from

        Returns:
            List of long-running tool names
        """

        long_running_tool_names = []

        if hasattr(agent, "tools") and agent.tools:
            # Handle both single tool and list of tools
            tools = agent.tools if isinstance(agent.tools, (list, tuple)) else [agent.tools]

            for tool in tools:
                # Check if it's a LongRunningFunctionTool
                if isinstance(tool, LongRunningFunctionTool):
                    long_running_tool_names.append(tool.name)
                # Check if it's a ClientProxyToolset (all its tools are long-running)
                elif isinstance(tool, ClientProxyToolset):
                    # Extract tool names from the toolset
                    for ag_ui_tool in tool.ag_ui_tools:
                        long_running_tool_names.append(ag_ui_tool.name)
                        logger.debug("Added long-running tool from ClientProxyToolset: %s", ag_ui_tool.name)

        logger.debug("Extracted long-running tool names: %s", long_running_tool_names)
        return long_running_tool_names

    def _create_runner(self, agent: BaseAgent, user_id: str, app_name: str) -> Runner:
        """Create a new runner instance."""
        return Runner(
            app_name=app_name,
            agent=agent,
            session_service=self._session_manager._session_service,
            memory_service=self._memory_service,
        )

    async def cancel_run(
        self,
        session_id: str,
        app_name: str,
        user_id: str,
    ) -> bool:
        """Cancel an ongoing run for the specified session.

        Called when the SSE connection is closed by the client.

        Args:
            session_id: The thread/session ID
            app_name: Application name
            user_id: User identifier

        Returns:
            True if cancellation was triggered successfully
        """

        # Use configured cancel_wait_timeout
        timeout = self._cancel_wait_timeout

        logger.debug("Cancelling run for session %s", session_id)

        # Cancel the TRPC run using the existing mechanism
        cleanup_event = await cancel.cancel_run(app_name, user_id, session_id)

        if cleanup_event is None:
            # No active run found - might have already completed
            logger.debug("No active run to cancel for session %s", session_id)
            return False

        try:
            # Wait for cleanup to complete
            await asyncio.wait_for(cleanup_event.wait(), timeout=timeout)
            logger.info("Cancel completed for session %s", session_id)
        except asyncio.TimeoutError:
            logger.warning("Cancel timeout for session %s", session_id)

        # Clear pending tool calls from session state
        try:
            await self._session_manager.set_state_value(
                session_id=session_id,
                app_name=app_name,
                user_id=user_id,
                key="pending_tool_calls",
                value=[],  # Clear all pending tool calls
            )
            logger.debug("Cleared pending tool calls for session %s", session_id)
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Failed to clear pending tool calls: %s", ex)

        # Also cancel the ExecutionState task if still running
        async with self._execution_lock:
            if session_id in self._active_executions:
                execution = self._active_executions[session_id]
                await execution.cancel()
                del self._active_executions[session_id]
                logger.debug("Cancelled and removed execution state for session %s", session_id)

        return True

    async def run(self,
                  input: RunAgentInput,
                  http_request: Optional[Request] = None) -> AsyncGenerator[BaseEvent, None]:
        """Run the TRPC agent with client-side tool support.

        All client-side tools are long-running. For tool result submissions,
        we continue existing executions. For new requests, we start new executions.
        TRPC sessions handle conversation continuity and tool result processing.

        Args:
            input: The AG-UI run input
            http_request: Optional HTTP request for this run.

        Yields:
            AG-UI protocol events
        """
        # Check if this is a tool result submission for an existing execution
        if self._is_tool_result_submission(input):
            # Handle tool results for existing execution
            async for event in self._handle_tool_result_submission(input, http_request):
                yield event
        else:
            # Start new execution for regular requests
            async for event in self._start_new_execution(input, http_request):
                yield event

    async def _ensure_session_exists(self, app_name: str, user_id: str, session_id: str, initial_state: dict):
        """Ensure a session exists, creating it if necessary via session manager."""
        try:
            # Use session manager to get or create session
            trpc_session = await self._session_manager.get_or_create_session(
                session_id=session_id,
                app_name=app_name,  # Use app_name for session management
                user_id=user_id,
                initial_state=initial_state,
            )

            # Update session lookup cache for efficient session ID to metadata mapping
            self._session_lookup_cache[session_id] = {"app_name": app_name, "user_id": user_id}

            logger.debug("Session ready: %s for user: %s", session_id, user_id)
            return trpc_session
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Failed to ensure session %s: %s", session_id, ex)
            raise

    async def _convert_latest_message(self, input: RunAgentInput) -> Optional[Content]:
        """Convert the latest user message to TRPC Content format.

        Supports both simple string content and multi-model content (text + images).
        """

        if not input.messages:
            return None

        # Get the latest user message
        for message in reversed(input.messages):
            if getattr(message, "role", None) == "user" and getattr(message, "content", None):
                parts = convert_message_content_to_parts(getattr(message, "content", None))
                if not parts:
                    return None
                return types.Content(role="user", parts=parts)

        return None

    def _is_tool_result_submission(self, input: RunAgentInput) -> bool:
        """Check if this request contains tool results.

        Args:
            input: The run input

        Returns:
            True if the last message is a tool result
        """
        if not input.messages:
            return False

        last_message = input.messages[-1]
        return hasattr(last_message, "role") and last_message.role == "tool"

    async def _handle_tool_result_submission(self,
                                             input: RunAgentInput,
                                             http_request: Optional[Request] = None) -> AsyncGenerator[BaseEvent, None]:
        """Handle tool result submission for existing execution.

        Args:
            input: The run input containing tool results
            http_request: Optional HTTP request for this run

        Yields:
            AG-UI events from continued execution
        """
        thread_id = input.thread_id

        # Extract tool results that is send by the frontend
        tool_results = await self._extract_tool_results(input)

        # if the tool results are not sent by the fronted then call the tool function
        if not tool_results:
            logger.error("Tool result submission without tool results for thread %s", thread_id)
            yield RunErrorEvent(type=EventType.RUN_ERROR,
                                message="No tool results found in submission",
                                code="NO_TOOL_RESULTS")
            return

        try:
            # Check if tool result matches any pending tool calls for better debugging
            for tool_result in tool_results:
                tool_call_id = tool_result["message"].tool_call_id
                has_pending = await self._has_pending_tool_calls(thread_id)

                if has_pending:
                    # Could add more specific check here for the exact tool_call_id
                    # but for now just log that we're processing a tool result while tools are pending
                    logger.debug("Processing tool result %s for thread %s with pending tools", tool_call_id, thread_id)
                    # Remove from pending tool calls now that we're processing it
                    await self._remove_pending_tool_call(thread_id, tool_call_id)
                else:
                    # No pending tools - this could be a stale result or from a different session
                    logger.warning("No pending tool calls found for tool result %s in thread %s", tool_call_id,
                                   thread_id)

            # Since all tools are long-running, all tool results are standalone
            # and should start new executions with the tool results
            logger.info("Starting new execution for tool result in thread %s", thread_id)
            async for event in self._start_new_execution(input, http_request):
                yield event

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error handling tool results: %s", ex, exc_info=True)
            yield RunErrorEvent(
                type=EventType.RUN_ERROR,
                message=f"Failed to process tool results: {str(ex)}",
                code="TOOL_RESULT_PROCESSING_ERROR",
            )

    async def _extract_tool_results(self, input: RunAgentInput) -> List[Dict]:
        """Extract tool messages with their names from input.

        Only extracts the most recent tool message to avoid accumulation issues
        where multiple tool results are sent to the LLM causing API errors.

        Args:
            input: The run input

        Returns:
            List of dicts containing tool name and message (single item for most recent)
        """
        # Create a mapping of tool_call_id to tool name
        tool_call_map = {}
        for message in input.messages:
            if hasattr(message, "tool_calls") and message.tool_calls:
                for tool_call in message.tool_calls:
                    tool_call_map[tool_call.id] = tool_call.function.name

        # Find the most recent tool message (should be the last one in a tool result submission)
        most_recent_tool_message = None
        for message in reversed(input.messages):
            if hasattr(message, "role") and message.role == "tool":
                most_recent_tool_message = message
                break

        if most_recent_tool_message:
            tool_name = tool_call_map.get(most_recent_tool_message.tool_call_id, "unknown")

            # Debug: Log the extracted tool message
            logger.debug("Extracted most recent ToolMessage: role=%s, tool_call_id=%s, content='%s'",
                         most_recent_tool_message.role, most_recent_tool_message.tool_call_id,
                         most_recent_tool_message.content)

            return [{"tool_name": tool_name, "message": most_recent_tool_message}]

        return []

    async def _stream_events(self, execution: ExecutionState) -> AsyncGenerator[BaseEvent, None]:
        """Stream events from execution queue.

        Args:
            execution: The execution state

        Yields:
            AG-UI events from the queue
        """
        logger.debug("Starting _stream_events for thread %s, queue ID: %s", execution.thread_id,
                     id(execution.event_queue))
        event_count = 0
        timeout_count = 0

        while True:
            try:
                logger.debug("Waiting for event from queue (thread %s, queue size: %s)", execution.thread_id,
                             execution.event_queue.qsize())

                # Wait for event with timeout
                event = await asyncio.wait_for(execution.event_queue.get(), timeout=1.0)  # Check every second

                event_count += 1
                logger.debug("Got event #%s from queue: %s (thread %s)", event_count,
                             type(event).__name__ if event else 'None', execution.thread_id)

                if event is None:
                    # Execution complete
                    execution.is_complete = True
                    logger.debug("Execution complete for thread %s after %s events", execution.thread_id, event_count)
                    break

                logger.debug("Streaming event #%s: %s (thread %s)", event_count,
                             type(event).__name__, execution.thread_id)
                yield event

            except asyncio.TimeoutError:
                timeout_count += 1
                logger.debug("Timeout #%s waiting for events (thread %s, task done: %s, queue size: %s)", timeout_count,
                             execution.thread_id, execution.task.done(), execution.event_queue.qsize())

                # Check if execution is stale
                if execution.is_stale(self._execution_timeout):
                    logger.error("Execution timed out for thread %s", execution.thread_id)
                    yield RunErrorEvent(type=EventType.RUN_ERROR,
                                        message="Execution timed out",
                                        code="EXECUTION_TIMEOUT")
                    break

                # Check if task is done
                if execution.task.done():
                    # Task completed but didn't send None
                    execution.is_complete = True
                    try:
                        task_result = execution.task.result()
                        logger.debug("Task completed with result: %s (thread %s)", task_result, execution.thread_id)
                    except Exception as ex:  # pylint: disable=broad-except
                        logger.debug("Task completed with exception: %s (thread %s)", ex, execution.thread_id)

                    # Wait a bit more in case there are events still coming
                    logger.debug(
                        "Task done but no None signal - checking queue one more time (thread %s, queue size: %s)",
                        execution.thread_id, execution.event_queue.qsize())
                    if execution.event_queue.qsize() > 0:
                        logger.debug("Found %s events in queue after task completion, continuing...",
                                     execution.event_queue.qsize())
                        continue

                    logger.debug("Task completed without sending None signal (thread %s)", execution.thread_id)
                    break

    async def _is_hitl_text_scenario(self, thread_id: str, app_name: str, user_id: str) -> Optional[types.FunctionCall]:
        """Check if this is a HITL scenario with text instead of tool_result.

        HITL pattern: last two events in session are (function_call, function_response)
        and the function IDs match.

        Args:
            thread_id: Session/thread ID
            app_name: Application name
            user_id: User identifier

        Returns:
            The FunctionCall object if HITL pattern detected, None otherwise
        """
        try:
            session = await self._session_manager._session_service.get_session(
                session_id=thread_id,
                app_name=app_name,
                user_id=user_id,
            )

            if not session or not session.events or len(session.events) < 2:
                return None

            # Check last two events
            last_event = session.events[-1]
            second_last_event = session.events[-2]

            # Check if second last event has function_call
            function_call = None
            if second_last_event.content and second_last_event.content.parts:
                for part in reversed(second_last_event.content.parts):
                    if part.function_call:
                        function_call = part.function_call
                        break

            # Check if last event has function_response
            function_response = None
            if last_event.content and last_event.content.parts:
                for part in reversed(last_event.content.parts):
                    if part.function_response:
                        function_response = part.function_response
                        break

            # HITL pattern: (function_call, function_response) with matching IDs
            if function_call and function_response:
                if function_call.id == function_response.id:
                    return function_call

            return None

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error checking HITL scenario: %s", ex, exc_info=True)
            return None

    async def _start_new_execution(self,
                                   input: RunAgentInput,
                                   http_request: Optional[Request] = None) -> AsyncGenerator[BaseEvent, None]:
        """Start a new TRPC execution with tool support.

        Args:
            input: The run input
            http_request: Optional HTTP request for this run

        Yields:
            AG-UI events from the execution
        """
        try:
            # Emit RUN_STARTED
            logger.debug("Emitting RUN_STARTED for thread %s, run %s", input.thread_id, input.run_id)
            yield RunStartedEvent(type=EventType.RUN_STARTED, thread_id=input.thread_id, run_id=input.run_id)

            # Check concurrent execution limit
            async with self._execution_lock:
                if len(self._active_executions) >= self._max_concurrent:
                    # Clean up stale executions
                    await self._cleanup_stale_executions()

                    if len(self._active_executions) >= self._max_concurrent:
                        raise RuntimeError(f"Maximum concurrent executions ({self._max_concurrent}) reached")

                # Check if there's an existing execution for this thread and wait for it
                existing_execution = self._active_executions.get(input.thread_id)

            # If there was an existing execution, wait for it to complete
            if existing_execution and not existing_execution.is_complete:
                logger.debug("Waiting for existing execution to complete for thread %s", input.thread_id)
                try:
                    await existing_execution.task
                except Exception as ex:  # pylint: disable=broad-except
                    logger.debug("Previous execution completed with error: %s", ex)

            # Start background execution
            execution = await self._start_background_execution(input, http_request)

            # Store execution (replacing any previous one)
            async with self._execution_lock:
                self._active_executions[input.thread_id] = execution

            # Stream events and track tool calls
            logger.debug("Starting to stream events for execution %s", execution.thread_id)
            has_tool_calls = False
            tool_call_ids = []

            logger.debug("About to iterate over _stream_events for execution %s", execution.thread_id)
            async for event in self._stream_events(execution):
                # Track tool calls for HITL scenarios
                if isinstance(event, ToolCallEndEvent):
                    logger.debug("Detected ToolCallEndEvent with id: %s", event.tool_call_id)
                    has_tool_calls = True
                    tool_call_ids.append(event.tool_call_id)

                # backend tools will always emit ToolCallResultEvent
                # If it is a backend tool then we don't need to add the tool_id in pending_tools
                if isinstance(event, ToolCallResultEvent) and event.tool_call_id in tool_call_ids:
                    logger.debug("Detected ToolCallResultEvent with id: %s", event.tool_call_id)
                    tool_call_ids.remove(event.tool_call_id)

                logger.debug("Yielding event: %s", type(event).__name__)
                yield event

            logger.debug("Finished iterating over _stream_events for execution %s", execution.thread_id)

            # If we found tool calls, add them to session state BEFORE cleanup
            if has_tool_calls:
                app_name = self.get_app_name(input)
                user_id = self.get_user_id(input)
                for tool_call_id in tool_call_ids:
                    await self._add_pending_tool_call_with_context(
                        execution.thread_id,
                        tool_call_id,
                        app_name,
                        user_id,
                    )
            logger.debug("Finished streaming events for execution %s", execution.thread_id)

            # Emit RUN_FINISHED
            logger.debug("Emitting RUN_FINISHED for thread %s, run %s", input.thread_id, input.run_id)
            yield RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=input.thread_id, run_id=input.run_id)

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error in new execution: %s", ex, exc_info=True)
            yield RunErrorEvent(type=EventType.RUN_ERROR, message=str(ex), code="EXECUTION_ERROR")
        finally:
            # Clean up execution if complete and no pending tool calls (HITL scenarios)
            async with self._execution_lock:
                if input.thread_id in self._active_executions:
                    execution = self._active_executions[input.thread_id]
                    execution.is_complete = True

                    # Check if session has pending tool calls before cleanup
                    has_pending = await self._has_pending_tool_calls(input.thread_id)
                    if not has_pending:
                        del self._active_executions[input.thread_id]
                        logger.debug("Cleaned up execution for thread %s", input.thread_id)
                    else:
                        logger.info("Preserving execution for thread %s - has pending tool calls (HITL scenario)",
                                    input.thread_id)

    async def _start_background_execution(self,
                                          input: RunAgentInput,
                                          http_request: Optional[Request] = None) -> ExecutionState:
        """Start TRPC execution in background with tool support.

        Args:
            input: The run input
            http_request: Optional HTTP request for this run

        Returns:
            ExecutionState tracking the background execution
        """
        event_queue = asyncio.Queue()
        logger.debug("Created event queue %s for thread %s", id(event_queue), input.thread_id)
        # Extract necessary information
        user_id = self.get_user_id(input)
        app_name = self.get_app_name(input)

        # Prepare agent modifications (SystemMessage and tools)
        agent_updates = {}

        # Handle SystemMessage if it's the first message - append to agent instructions
        if input.messages and isinstance(input.messages[0], SystemMessage):
            system_content = input.messages[0].content
            if system_content:
                current_instruction = getattr(self._trpc_agent, "instruction", "")

                if callable(current_instruction):
                    # Handle instructions provider
                    if inspect.iscoroutinefunction(current_instruction):
                        # Async instruction provider
                        async def instruction_provider_wrapper_async(*args, **kwargs):
                            instructions = system_content
                            original_instructions = await current_instruction(*args, **kwargs) or ""
                            if original_instructions:
                                instructions = f"{original_instructions}\n\n{instructions}"
                            return instructions

                        new_instruction = instruction_provider_wrapper_async
                    else:
                        # Sync instruction provider
                        def instruction_provider_wrapper_sync(*args, **kwargs):
                            instructions = system_content
                            original_instructions = current_instruction(*args, **kwargs) or ""
                            if original_instructions:
                                instructions = f"{original_instructions}\n\n{instructions}"
                            return instructions

                        new_instruction = instruction_provider_wrapper_sync

                    logger.debug("Will wrap callable InstructionProvider and append SystemMessage: '%s...'",
                                 system_content[:100])
                else:
                    # Handle string instructions
                    if current_instruction:
                        new_instruction = f"{current_instruction}\n\n{system_content}"
                    else:
                        new_instruction = system_content
                    logger.debug("Will append SystemMessage to string instructions: '%s...'", system_content[:100])

                agent_updates["instruction"] = new_instruction

        # Create dynamic toolset if tools provided and prepare tool updates
        toolset = None
        if input.tools:

            # Get existing tools from the agent
            existing_tools = []
            agent_tools = getattr(self._trpc_agent, "tools", [])
            if agent_tools:
                if isinstance(agent_tools, (list, tuple)):
                    existing_tools = list(agent_tools)
                else:
                    existing_tools = [agent_tools]

            # if same tool is defined in frontend and backend then agent will only use the backend tool
            input_tools = []
            for input_tool in input.tools:
                # Check if this input tool's name matches any existing tool
                # Also exclude this specific tool call "transfer_to_agent"
                # which is used internally by the trpc to handoff to other agents
                if (not any(
                        hasattr(existing_tool, "__name__") and input_tool.name == existing_tool.__name__
                        for existing_tool in existing_tools) and input_tool.name != "transfer_to_agent"):
                    input_tools.append(input_tool)

            toolset = ClientProxyToolset(ag_ui_tools=input_tools, event_queue=event_queue)

            # Combine existing tools with our proxy toolset
            combined_tools = existing_tools + [toolset]
            agent_updates["tools"] = combined_tools
            logger.debug("Will combine %s existing tools with proxy toolset", len(existing_tools))

        # Create a single copy of the agent with all updates if any modifications needed
        agent = self._trpc_agent
        if agent_updates:
            agent = self._trpc_agent.model_copy(update=agent_updates)
            logger.debug("Created modified agent copy with updates: %s", list(agent_updates.keys()))

        # Create background task
        logger.debug("Creating background task for thread %s", input.thread_id)
        task = asyncio.create_task(
            self._run_trpc_in_background(
                input=input,
                agent=agent,
                user_id=user_id,
                app_name=app_name,
                event_queue=event_queue,
                http_request=http_request,
            ))
        logger.debug("Background task created for thread %s: %s", input.thread_id, task)

        return ExecutionState(task=task, thread_id=input.thread_id, event_queue=event_queue)

    async def _run_trpc_in_background(self,
                                      input: RunAgentInput,
                                      agent: BaseAgent,
                                      user_id: str,
                                      app_name: str,
                                      event_queue: asyncio.Queue,
                                      http_request: Optional[Request] = None):
        """Run TRPC agent in background, emitting events to queue.

        Args:
            input: The run input
            agent: The TRPC agent to run (already prepared with tools and SystemMessage)
            user_id: User ID
            app_name: App name
            event_queue: Queue for emitting events
            http_request: Optional HTTP request for this run
        """
        try:
            # Agent is already prepared with tools and SystemMessage instructions (if any)
            # from _start_background_execution, so no additional agent copying needed here

            # Create runner
            runner = self._create_runner(agent=agent, user_id=user_id, app_name=app_name)

            # Create RunConfig
            run_config = self._run_config_factory(input)
            if http_request:
                set_agui_http_req(run_config, http_request)

            # Ensure session exists
            await self._ensure_session_exists(app_name, user_id, input.thread_id, input.state)

            # this will always update the backend states with the frontend states
            # Recipe Demo Example: if there is a state "salt" in the ingredients state and in frontend user
            # remove this salt state using UI from the ingredients list then our backend should also update
            # these state changes as well to sync both the states
            await self._session_manager.update_session_state(input.thread_id, app_name, user_id, input.state)

            # Convert messages
            # only use this new_message if there is no tool response from the user
            new_message = await self._convert_latest_message(input)

            # Check if this is HITL scenario: last two events are (function_call, function_response)
            # and user sent text instead of tool_result. Returns the function_call if HITL detected.
            hitl_function_call = await self._is_hitl_text_scenario(input.thread_id, app_name, user_id)

            # if there is a tool response submission by the user then we need to only pass
            # the tool response to the trpc runner
            if self._is_tool_result_submission(input):
                tool_results = await self._extract_tool_results(input)
                parts = []
                for tool_msg in tool_results:
                    tool_call_id = tool_msg["message"].tool_call_id
                    content = tool_msg["message"].content

                    # Debug: Log the actual tool message content we received
                    logger.debug("Received tool result for call %s: content='%s', type=%s", tool_call_id, content,
                                 type(content))

                    # Call user feedback handler if provided (may modify content)
                    content = await self._execute_user_feedback_handler(
                        tool_name=tool_msg["tool_name"],
                        tool_message=content,
                        thread_id=input.thread_id,
                        app_name=app_name,
                        user_id=user_id,
                    )

                    # Parse JSON content, handling empty or invalid JSON gracefully
                    try:
                        if content and content.strip():
                            result = json.loads(content)
                        else:
                            # Handle empty content as a success with empty result
                            result = {"success": True, "result": None}
                            logger.warning("Empty tool result content for tool call %s, using empty success result",
                                           tool_call_id)
                    except json.JSONDecodeError as ex:
                        # Handle invalid JSON by providing detailed error result
                        logger.debug("Invalid JSON %s in tool result: %s", content, ex)
                        # At this time, directly use content as the result
                        result = {"content": content}

                    updated_function_response_part = types.Part(function_response=types.FunctionResponse(
                        id=tool_call_id,
                        name=tool_msg["tool_name"],
                        response=result,
                    ))
                    parts.append(updated_function_response_part)
                new_message = types.Content(parts=parts, role="user")
            elif hitl_function_call:
                # Handle HITL scenario where frontend sent text instead of tool_result
                # Convert the user text to function_response
                # hitl_function_call already contains the function_call from the session
                if new_message and new_message.parts:
                    # Extract user text
                    user_text = "".join(part.text for part in new_message.parts if part.text)
                    tool_name = hitl_function_call.name
                    tool_call_id = hitl_function_call.id

                    # Call user feedback handler if provided
                    content = await self._execute_user_feedback_handler(
                        tool_name=tool_name,
                        tool_message=user_text,
                        thread_id=input.thread_id,
                        app_name=app_name,
                        user_id=user_id,
                    )

                    # Convert text to function_response format
                    # Try to parse as JSON, otherwise wrap in a dict
                    try:
                        if content and content.strip():
                            result = json.loads(content)
                            # content="1" is a valid json, but we expect a dict
                            if not isinstance(result, dict):
                                result = {"content": result}
                        else:
                            result = {"content": user_text}
                    except json.JSONDecodeError:
                        # Not JSON, wrap the text content
                        result = {"content": content if content else user_text}

                    # Create function_response with same ID as function_call
                    # This will override the previous function_response in the session
                    function_response_part = types.Part(function_response=types.FunctionResponse(
                        id=tool_call_id,
                        name=tool_name,
                        response=result,
                    ))

                    new_message = types.Content(parts=[function_response_part], role="user")
                    logger.info("Converted HITL text to function_response for tool_call_id=%s", tool_call_id)

            # Extract long-running tool names from the agent
            long_running_tool_names = self._extract_long_running_tool_names(agent)

            # Create event translator
            event_translator = EventTranslator(long_running_tool_names=long_running_tool_names)

            # Validate new_message before running agent
            if not new_message or not new_message.parts:
                error_msg = "No user message found in request"
                logger.error(error_msg)
                error_event = RunErrorEvent(
                    type=EventType.RUN_ERROR,
                    message=error_msg,
                    code="NO_MESSAGE_ERROR",
                )
                await event_queue.put(error_event)
                return

            # Run TRPC agent
            async for trpc_event in runner.run_async(user_id=user_id,
                                                     session_id=input.thread_id,
                                                     new_message=new_message,
                                                     run_config=run_config):
                if not isinstance(trpc_event, LongRunningEvent):
                    # Check if custom translator should handle this event
                    if self._custom_event_translator and self._custom_event_translator.need_translate(trpc_event):
                        # Import context class here to avoid circular imports
                        from .._plugin._langgraph_event_translator import AgUiTranslationContext
                        translator_context = AgUiTranslationContext(thread_id=input.thread_id, run_id=input.run_id)
                        async for ag_ui_event in self._custom_event_translator.translate(
                                trpc_event, translator_context):
                            await event_queue.put(ag_ui_event)
                    else:
                        # Existing behavior for regular events
                        async for ag_ui_event in event_translator.translate(trpc_event, input.thread_id, input.run_id):
                            logger.debug("Emitting event to queue: %s (thread %s, queue size before: %s)",
                                         type(ag_ui_event).__name__, input.thread_id, event_queue.qsize())
                            await event_queue.put(ag_ui_event)
                            logger.debug("Event queued: %s (thread %s, queue size after: %s)",
                                         type(ag_ui_event).__name__, input.thread_id, event_queue.qsize())
                else:
                    # LongRunning Tool events are usually emitted in final response
                    async for ag_ui_event in event_translator.translate_lro_function_calls(trpc_event):
                        await event_queue.put(ag_ui_event)
                        logger.debug("Event queued: %s (thread %s, queue size after: %s)",
                                     type(ag_ui_event).__name__, input.thread_id, event_queue.qsize())

            # Force close any streaming messages
            async for ag_ui_event in event_translator.force_close_streaming_message():
                await event_queue.put(ag_ui_event)
            # moving states snapshot events after the text event closure
            # to avoid this error https://github.com/Contextable/ag-ui/issues/28
            final_state = await self._session_manager.get_session_state(input.thread_id, app_name, user_id)
            if final_state:
                from datetime import datetime

                current_timestamp = datetime.now().timestamp()
                ag_ui_event = event_translator._create_state_snapshot_event(final_state, current_timestamp)
                await event_queue.put(ag_ui_event)
            # Signal completion - TRPC execution is done
            logger.debug("Background task sending completion signal for thread %s", input.thread_id)
            await event_queue.put(None)
            logger.debug("Background task completion signal sent for thread %s", input.thread_id)

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Background execution error: %s", ex, exc_info=True)
            # Put error in queue
            await event_queue.put(
                RunErrorEvent(type=EventType.RUN_ERROR, message=str(ex), code="BACKGROUND_EXECUTION_ERROR"))
            await event_queue.put(None)
        finally:
            # Background task cleanup completed
            # Note: toolset cleanup is handled by garbage collection
            # since toolset is now embedded in the agent's tools
            pass

    async def _execute_user_feedback_handler(self, tool_name: str, tool_message: str, thread_id: str, app_name: str,
                                             user_id: str) -> str:
        """Execute the user feedback handler if configured.

        Args:
            tool_name: Name of the tool that was executed
            tool_message: Content/result from the tool execution
            thread_id: Current session/thread ID
            app_name: Application name
            user_id: User identifier

        Returns:
            The potentially modified tool_message
        """
        if not self._user_feedback_handler:
            return tool_message

        try:
            # Get session for handler
            session = await self._session_manager._session_service.get_session(session_id=thread_id,
                                                                               app_name=app_name,
                                                                               user_id=user_id)

            if not session:
                logger.warning("Session %s not found for user feedback handler", thread_id)
                return tool_message

            # Create feedback content
            feedback_content = AgUiUserFeedBack(
                session=session,
                tool_name=tool_name,
                tool_message=tool_message,
            )

            # Call user feedback handler
            await self._user_feedback_handler(feedback_content)

            # If session was modified, update it
            if feedback_content.check_session_modified():
                await self._session_manager._session_service.update_session(session)
                logger.debug("Updated session %s via user feedback handler", thread_id)

            # Return potentially modified tool_message
            return feedback_content.tool_message

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error in user feedback handler: %s", ex, exc_info=True)
            return tool_message

    async def _cleanup_stale_executions(self):
        """Clean up stale executions."""
        stale_threads = []

        for thread_id, execution in self._active_executions.items():
            if execution.is_stale(self._execution_timeout):
                stale_threads.append(thread_id)

        for thread_id in stale_threads:
            execution = self._active_executions.pop(thread_id)
            await execution.cancel()
            logger.info("Cleaned up stale execution for thread %s", thread_id)

    async def close(self):
        """Clean up resources including active executions."""
        # Cancel all active executions
        async with self._execution_lock:
            for execution in self._active_executions.values():
                await execution.cancel()
            self._active_executions.clear()

        # Clear session lookup cache
        self._session_lookup_cache.clear()

        # Stop session manager cleanup task
        await self._session_manager.stop_cleanup_task()
