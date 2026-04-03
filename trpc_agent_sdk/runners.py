# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Core runner implementations for TRPC Agent framework.

This module provides the main entry point for agent execution, orchestrating
the entire flow including service management, context creation, and agent lifecycle.
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator
from typing import Optional

from trpc_agent_sdk import cancel
from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.artifacts import BaseArtifactService
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.context import new_invocation_context_id
from trpc_agent_sdk.events import AgentCancelledEvent
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.exceptions import RunCancelledException
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.memory import BaseMemoryService
from trpc_agent_sdk.sessions import BaseSessionService
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.telemetry import tracer
from trpc_agent_sdk.telemetry._trace import trace_cancellation
from trpc_agent_sdk.telemetry._trace import trace_runner
from trpc_agent_sdk.tools import BaseToolSet
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


class Runner:
    """The Runner class is used to run agents.

    It manages the execution of an agent within a session, handling message
    processing, event generation, and interaction with various services like
    artifact storage, session management, and memory.

    Attributes:
        app_name: The application name of the runner.
        agent: The root agent to run.
        artifact_service: The artifact service for the runner.
        session_service: The session service for the runner.
        memory_service: The memory service for the runner.
    """

    app_name: str
    """The app name of the runner."""
    agent: BaseAgent
    """The root agent to run."""
    artifact_service: Optional[BaseArtifactService] = None
    """The artifact service for the runner."""
    session_service: BaseSessionService
    """The session service for the runner."""
    memory_service: Optional[BaseMemoryService] = None
    """The memory service for the runner."""

    def __init__(
        self,
        *,
        app_name: str,
        agent: BaseAgent,
        session_service: BaseSessionService,
        artifact_service: Optional[BaseArtifactService] = None,
        memory_service: Optional[BaseMemoryService] = None,
    ):
        """Initializes the Runner.

        Args:
            app_name: The application name of the runner.
            agent: The root agent to run.
            artifact_service: The artifact service for the runner.
            session_service: The session service for the runner.
            memory_service: The memory service for the runner.
        """
        self.app_name = app_name
        self.agent = agent
        self.artifact_service = artifact_service
        self.session_service = session_service
        self.memory_service = memory_service

    async def cancel_run_async(
        self,
        user_id: str,
        session_id: str,
        timeout: float = 1.0,
    ) -> bool:
        """Cancel a running run for the specified session.

        This method requests cancellation of an ongoing agent execution.
        The cancellation is cooperative - the agent will stop at the next
        cancellation checkpoint.

        Args:
            user_id: The user ID of the session to cancel.
            session_id: The session ID to cancel.
            timeout: Timeout in seconds to wait for cancellation to complete.
                This method will wait until the run is cleaned up
                (i.e., AgentCancelledEvent is processed) or the timeout is reached.
                Default is 1.0 seconds.

        Returns:
            True if an active run was found and marked for cancellation,
            False if no active run found for this session.

        Example:
            runner = Runner(app_name="my_app", agent=agent, ...)

            # Start agent in background task
            async def run_agent():
                async for event in runner.run_async(
                    user_id="user1",
                    session_id="session1",
                    new_message=...
                ):
                    process(event)

            task = asyncio.create_task(run_agent())

            # Later, cancel the run using same user_id and session_id
            # Wait up to 2 seconds for cancellation to complete
            success = await runner.cancel_run_async(
                user_id="user1",
                session_id="session1",
                timeout=2.0
            )
            print(f"Cancellation requested: {success}")
        """
        # cancel_run returns an asyncio.Event that will be set when cleanup_run is called,
        # or None if no active run found
        cleanup_event = await cancel.cancel_run(self.app_name, user_id, session_id)

        if cleanup_event is None:
            return False

        try:
            await asyncio.wait_for(cleanup_event.wait(), timeout=timeout)
            logger.info("Cancel completed for user_id %s, session %s", user_id, session_id)
        except asyncio.TimeoutError:
            logger.warning(
                "Cancel wait timeout (%ss) reached for user_id %s, session %s. The execution may still be running.",
                timeout, user_id, session_id)

        return True

    async def run_async(
        self,
        *,
        user_id: str,
        session_id: str,
        new_message: Content | list[Content],
        run_config: RunConfig = RunConfig(),
        agent_context: Optional[AgentContext] = None,
    ) -> AsyncGenerator[Event, None]:
        """Main entry method to run the agent in this runner.

        Args:
            user_id: The user ID of the session.
            session_id: The session ID of the session.
            new_message: A new message to append to the session.
            run_config: The run config for the agent.
            agent_context: The agent context for user interaction control. If not provided,
                         a default one will be created.

        Yields:
            The events generated by the agent.
        """
        # Avoid start_as_current_span in async generators; cancellation may close
        # the generator from another context and trigger detach token errors.
        span = tracer.start_span("invocation")
        try:
            # Create default agent context if not provided
            if agent_context is None:
                agent_context = new_agent_context()

            session = await self.session_service.get_session(app_name=self.app_name,
                                                             user_id=user_id,
                                                             session_id=session_id,
                                                             agent_context=agent_context)
            if not session:
                # Create new session if not found - use create_session instead of save_session
                session = await self.session_service.create_session(app_name=self.app_name,
                                                                    user_id=user_id,
                                                                    session_id=session_id,
                                                                    agent_context=agent_context)
                logger.debug("Created new session: %s", session.id)
            else:
                logger.debug("Using existing session: %s with %s events", session.id, len(session.events))

            # Capture state before runner execution
            state_begin = dict(session.state)

            session.conversation_count += 1
            history_content: Content | None = None
            if isinstance(new_message, list):
                user_message = new_message[-1]
                history_content = new_message[0]
            else:
                user_message = new_message
            invocation_context = self._new_invocation_context(
                session,
                new_message=user_message,
                run_config=run_config,
                agent_context=agent_context,
            )
            root_agent = self.agent
            if run_config.save_history_enabled and history_content:
                history_event = Event(content=history_content,
                                      invocation_id=invocation_context.invocation_id,
                                      id=Event.new_id(),
                                      author=root_agent.name)
                await self.session_service.append_event(session=session, event=history_event)

            if user_message:
                await self._append_new_message_to_session(
                    session,
                    user_message,
                    invocation_context,
                )

            invocation_context.agent = self._find_agent_to_run(session, root_agent, run_config)

            # Register for cancellation tracking
            session_key = await cancel.register_run(
                app_name=self.app_name,
                user_id=user_id,
                session_id=session_id,
            )

            # Store session_key in invocation_context for use by agents
            invocation_context.session_key = session_key

            # Track the last non-streaming event for tracing
            last_non_streaming_event = None

            # Track accumulated partial text for cancellation handling
            temp_text = ""

            try:
                # Support multiple levels of agent transfers
                while True:
                    current_agent = invocation_context.agent
                    logger.debug("Running agent: %s", current_agent.name)

                    transfer_requested = False
                    async for event in current_agent.run_async(invocation_context):
                        # Track partial text accumulation
                        if event.partial:
                            if event.content and event.content.parts:
                                for part in event.content.parts:
                                    if part.text:
                                        temp_text += part.text
                        else:
                            # Clear temp_text on full event
                            temp_text = ""

                        if not event.partial:
                            await self.session_service.append_event(session=session, event=event)
                            # Track the last non-streaming event with content for tracing
                            # This excludes state update events which have content=None
                            if event.content is not None:
                                last_non_streaming_event = event

                        # Skip yielding events that are not visible
                        if not event.visible:
                            if event.actions.transfer_to_agent:
                                raise ValueError("Agent transfer requested but invisible is not allowed.")
                            continue

                        if run_config.streaming:
                            yield event
                        else:
                            if not event.partial:
                                yield event

                        # Handle agent transfer if requested
                        if event.actions.transfer_to_agent:
                            transfer_target = event.actions.transfer_to_agent
                            logger.debug("Processing agent transfer from %s to: %s", current_agent.name,
                                         transfer_target)

                            # Check if transferring to the same agent
                            if transfer_target == current_agent.name:
                                logger.warning(
                                    "Transfer to same agent '%s' detected, add 'already on agent' message to let agent continue",
                                    transfer_target)
                                already_in_event = Event(
                                    invocation_id=invocation_context.invocation_id,
                                    author=current_agent.name,
                                    partial=False,
                                    content=Content(parts=[
                                        Part(text=f"You are already at the {current_agent.name}. Continue execution.")
                                    ]),
                                )
                                await self.session_service.append_event(session=session, event=already_in_event)
                                transfer_requested = True
                                continue

                            # Find the target agent
                            target_agent = root_agent.find_agent(transfer_target)
                            if not target_agent:
                                logger.error("Transfer target agent '%s' not found in agent tree", transfer_target)
                                error_event = Event(
                                    invocation_id=invocation_context.invocation_id,
                                    author=current_agent.name,
                                    error_message=f"Transfer target agent '{transfer_target}' not found",
                                    error_code="transfer_target_not_found",
                                )
                                await self.session_service.append_event(session=session, event=error_event)
                                yield error_event
                                return

                            # Update the invocation context to use the target agent
                            invocation_context.agent = target_agent
                            # Update branch to reflect the new agent in the hierarchy
                            if current_agent.name == root_agent.name:
                                # Transferring from root agent to sub-agent
                                invocation_context.branch = f"{current_agent.name}.{target_agent.name}"
                            elif invocation_context.branch:
                                # Transferring between agents - construct branch from root to target
                                branch_parts = [root_agent.name]
                                agent = target_agent
                                agent_path = []
                                while agent != root_agent:
                                    agent_path.insert(0, agent.name)
                                    agent = agent.parent_agent
                                invocation_context.branch = ".".join(branch_parts + agent_path)
                            logger.debug("Successfully transferred to agent: %s, branch: %s", transfer_target,
                                         invocation_context.branch)

                            # Mark that transfer was requested and break from current agent's event loop
                            transfer_requested = True

                    # If no transfer was requested, we're done
                    if not transfer_requested:
                        logger.debug("No transfer requested by %s, ending execution", current_agent.name)
                        break

                # Trigger summarization if enabled
                await self.session_service.create_session_summary(session, ctx=invocation_context)
                if self.memory_service and self.memory_service.enabled:
                    await self.memory_service.store_session(session, agent_context=agent_context)

                # Compute state after runner execution
                state_end = dict(session.state)
                if last_non_streaming_event and last_non_streaming_event.actions and last_non_streaming_event.actions.state_delta:
                    state_end.update(last_non_streaming_event.actions.state_delta)

                # Call trace function with runner execution details
                trace_runner(
                    app_name=self.app_name,
                    user_id=user_id,
                    session_id=session_id,
                    invocation_context=invocation_context,
                    new_message=user_message,
                    last_event=last_non_streaming_event,
                    state_begin=state_begin,
                    state_end=state_end,
                )

            except RunCancelledException as ex:
                logger.info("Run for session %s was cancelled", session_id)
                logger.debug("Cancellation details: %s", ex, exc_info=True)

                # Capture partial state at cancellation point
                state_partial = dict(session.state)

                # Handle session cleanup for cancellation (two scenarios: streaming vs non-streaming)
                await cancel.handle_cancellation_session_cleanup(
                    session=session,
                    session_service=self.session_service,
                    invocation_id=invocation_context.invocation_id,
                    agent_name=invocation_context.agent.name,
                    branch=invocation_context.branch,
                    temp_text=temp_text,
                )
                await self.session_service.update_session(session=session)

                # Trace the cancellation event
                trace_cancellation(
                    app_name=self.app_name,
                    user_id=user_id,
                    session_id=session_id,
                    invocation_context=invocation_context,
                    reason=str(ex),
                    new_message=user_message,
                    last_event=last_non_streaming_event,
                    partial_text=temp_text,
                    state_begin=state_begin,
                    state_partial=state_partial,
                )

                # Yield cancellation event to notify client
                yield AgentCancelledEvent(
                    invocation_id=invocation_context.invocation_id,
                    author=invocation_context.agent.name,
                    reason=str(ex),
                    branch=invocation_context.branch,
                )

            finally:
                # Always cleanup cancellation tracking
                await cancel.cleanup_run(
                    app_name=self.app_name,
                    user_id=user_id,
                    session_id=session_id,
                )
        finally:
            span.end()

    async def _append_new_message_to_session(
        self,
        session: Session,
        new_message: Content,
        invocation_context: InvocationContext,
    ):
        """Appends a new message to the session.

        Args:
            session: The session to append the message to.
            new_message: The new message to append.
            invocation_context: The invocation context for the message.
        """
        if not new_message.parts:
            raise ValueError("No parts in the new_message.")

        # Create user event using the factory method
        event = Event(
            invocation_id=invocation_context.invocation_id,
            author="user",
            content=new_message,
        )

        # Add event to session using the session service
        await self.session_service.append_event(session=session, event=event)

    def _find_agent_to_run(
        self,
        session: Session,
        root_agent: BaseAgent,
        run_config: RunConfig,
    ) -> BaseAgent:
        """Finds the agent to run to continue the session.

        A qualified agent must be either of:
        - The root agent;
        - The last agent who replied in previous turns.
        - For human-in-the-loop scenarios: the agent who triggered the long-running operation.
        - When start_from_last_agent is True: always try the last responding agent
          from previous turns, regardless of transferability.

        Args:
            session: The session to find the agent for.
            root_agent: The root agent of the runner.
            run_config: The run config containing start_from_last_agent setting.

        Returns:
            The agent of the last message in the session or the root agent.
        """
        # Check for human-in-the-loop scenario: last event is user input with function_response
        if session.events:
            last_event = session.events[-1]

            # Check if last event is a user event with FunctionResponse
            if last_event.author == "user":
                # Extract the function_response from user input (only one expected)
                function_responses = last_event.get_function_responses()
                if function_responses:
                    user_function_response = function_responses[0]

                    # Search backwards for matching function_call
                    for event in reversed(session.events[:-1]):  # Exclude the last event
                        if event.author != "user":
                            function_calls = event.get_function_calls()
                            for function_call in function_calls:
                                if function_call.id == user_function_response.id:
                                    # Found matching function_call - resume from this agent
                                    agent_name = event.author
                                    logger.debug("Detected human-in-the-loop: function_call.id=%s, resuming agent: %s",
                                                 function_call.id, agent_name)

                                    if agent_name == root_agent.name:
                                        return root_agent

                                    agent = root_agent.find_agent(agent_name)
                                    if agent:
                                        logger.debug("Resuming agent %s after human-in-the-loop", agent_name)
                                        return agent
                                    else:
                                        logger.warning("Agent %s not found in agent tree, falling back to root agent",
                                                       agent_name)
                                        return root_agent

        # When start_from_last_agent is enabled, try to find the last responding agent
        if run_config.start_from_last_agent:
            for event in reversed(session.events):
                if event.author == "user":
                    continue
                if event.author == root_agent.name:
                    # Found root agent.
                    return root_agent
                if not (agent := root_agent.find_agent(event.author)):
                    # Agent not found, continue looking.
                    logger.warning(
                        "Event from an unknown agent: %s, event id: %s",
                        event.author,
                        event.invocation_id,
                    )
                    continue
                logger.debug("Starting from last agent: %s", agent.name)
                return agent
        # Falls back to root agent if no suitable agents are found in the session.
        return root_agent

    def _is_transferable_across_agent_tree(self, agent_to_run: BaseAgent) -> bool:
        """Whether the agent to run can transfer to any other agent in the agent tree.

        This typically means all agent_to_run's parent through root agent can
        transfer to their parent_agent.

        Args:
            agent_to_run: The agent to check for transferability.

        Returns:
            True if the agent can transfer, False otherwise.
        """
        # Import here to avoid circular imports
        from trpc_agent_sdk.agents._llm_agent import LlmAgent

        agent = agent_to_run
        while agent:
            # Only LLM-based Agent can provide agent transfer capability.
            if not isinstance(agent, LlmAgent):
                return False
            if agent.disallow_transfer_to_parent:
                return False
            # Get parent agent if available
            agent = agent.parent_agent
        return True

    def _new_invocation_context(
            self,
            session: Session,
            *,
            new_message: Optional[Content] = None,
            run_config: RunConfig = RunConfig(),
            agent_context: AgentContext,
    ) -> InvocationContext:
        """Creates a new invocation context.

        Args:
            session: The session for the context.
            new_message: The new message for the context.
            run_config: The run config for the context.
            agent_context: The agent context for user interaction control.

        Returns:
            The new invocation context.
        """
        invocation_id = new_invocation_context_id()

        return InvocationContext(
            artifact_service=self.artifact_service,
            session_service=self.session_service,
            memory_service=self.memory_service,
            invocation_id=invocation_id,
            agent=self.agent,
            agent_context=agent_context,
            session=session,
            user_content=new_message,
            run_config=run_config,
            branch=self.agent.name,  # Initialize root agent's branch
        )

    def _collect_toolset(self, agent: BaseAgent) -> set[BaseToolSet]:
        """Collect all toolset instances from the agent and its sub-agents recursively.

        Args:
            agent: The root agent to start collecting toolsets from

        Returns:
            A set containing all BaseToolSet instances found in the agent hierarchy
        """
        from .tools import BaseToolSet

        toolsets = set()
        tools = getattr(agent, "tools", [])
        for tool_union in tools:
            if isinstance(tool_union, BaseToolSet):
                toolsets.add(tool_union)
        for sub_agent in agent.get_subagents():
            toolsets.update(self._collect_toolset(sub_agent))
        return toolsets

    async def _cleanup_toolsets(self, toolsets_to_close: set[BaseToolSet]):
        """Safely close all provided toolsets.

        Args:
            toolsets_to_close: Set of BaseToolSet instances to close
        """
        if not toolsets_to_close:
            return

        for toolset in toolsets_to_close:
            try:
                logger.debug("Closing toolset: %s", type(toolset).__name__)
                await toolset.close()
                logger.debug("Successfully closed toolset: %s", type(toolset).__name__)
            except Exception as ex:  # pylint: disable=broad-except
                logger.error("Error closing toolset %s: %s", type(toolset).__name__, ex)

    async def close(self):
        """Gracefully close the runner and cleanup all resources.

        This will:
        1. Collect all toolset instances from the agent hierarchy
        2. Close each toolset with proper error handling
        3. Ensure all resources are released before shutdown
        """
        await self._cleanup_toolsets(self._collect_toolset(self.agent))
        if self.session_service:
            await self.session_service.close()
        if self.memory_service:
            await self.memory_service.close()
