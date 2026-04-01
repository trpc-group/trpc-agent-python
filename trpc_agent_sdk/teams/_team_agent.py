# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""TeamAgent implementation for TRPC Agent framework.

This module provides the TeamAgent class which coordinates multiple agent
members. A team leader (LLM) delegates tasks
to specialized member agents, then synthesizes their responses.

The TeamAgent appears as a normal BaseAgent to the Runner, but internally
manages delegation using a signal-based pattern for full control over
member execution flow.

Key Features:
- Event-detection-based delegation handling
- Controlled message building for members
- Optional history sharing between members
- State-based context management via session.state
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any
from typing import AsyncGenerator
from typing import Awaitable
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from pydantic import Field

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.events import LongRunningEvent
from trpc_agent_sdk.exceptions import RunCancelledException
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.skills import BaseSkillRepository
from trpc_agent_sdk.tools import LongRunningFunctionTool
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part

from ..agents import BaseAgent
from ..agents._llm_agent import InstructionProvider
from ..agents._llm_agent import LlmAgent
from ..agents._llm_agent import ToolUnion
from .core import DELEGATE_TOOL_NAME
from .core import DelegationSignal
from .core import TeamMemberMessageFilter
from .core import TeamMessageBuilder
from .core import TeamRunContext
from .core import create_delegate_to_member_tool
from .core import generate_team_leader_system_message
from .core import get_member_info_list
from .core import keep_all_member_message


class TeamAgent(BaseAgent):
    """Team Agent that coordinates multiple agent members.

    TeamAgent follows the Agno Team pattern: a team leader (LLM) delegates
    tasks to specialized member agents, then synthesizes their responses.

    Members can be any agent that inherits from BaseAgent (e.g., LlmAgent,
    another TeamAgent, or custom agents).

    The TeamAgent appears as a normal BaseAgent to the Runner, but internally
    manages delegation using a signal-based pattern.

    Key Design:
    - Leader runs with delegation tools that return DelegationSignal
    - After leader finishes, TeamAgent checks the last event to decide action
    - For delegation signals: execute member agent, update state, rerun leader
    - For normal completion: team execution is done
    - TeamRunContext is stored in session.state and updated via state_delta

    Usage:
        researcher = LlmAgent(name="researcher", model=model, ...)
        writer = LlmAgent(name="writer", model=model, ...)

        team = TeamAgent(
            name="content_team",
            model=model,
            members=[researcher, writer],
        )

        runner = Runner(app_name="app", agent=team, ...)
        async for event in runner.run_async(...):
            print(event.content)
    """

    # Core configuration
    members: List[BaseAgent] = Field(default_factory=list)
    """Team members (any agent inheriting from BaseAgent)."""

    model: Union[LLMModel, Callable[[dict[str, Any]], Awaitable[LLMModel]]] = ""
    """Model for the team leader's delegation decisions."""

    instruction: Union[str, InstructionProvider] = ""
    """Instructions for the team leader.

    Can be a static string or a callable that takes InvocationContext and returns
    instructions. The callable can be async.
    """

    tools: List[ToolUnion] = Field(default_factory=list)
    """Tools available to the team leader (in addition to delegation tools)."""

    skill_repository: Optional[BaseSkillRepository] = None
    """Skill repository for the team leader.

    When set, skills overview/body/docs can be injected into the internal
    leader LlmAgent request flow, matching standard LlmAgent skill behavior.
    """

    parallel_execution: bool = False
    """Execute multiple delegations in parallel (True) or sequentially (False)."""

    # History sharing configuration (for members)
    share_team_history: bool = False
    """Share team-level conversation history with members."""

    num_team_history_runs: int = 3
    """Number of past team runs to share with members."""

    share_member_interactions: bool = False
    """Share current run's member interactions with other members."""

    num_member_history_runs: int = 0
    """Number of past runs to include for member self history (0 means disabled)."""

    # History configuration (for leader - multi-turn support)
    add_history_to_leader: bool = True
    """Include conversation history from past invocations for the leader."""

    num_history_runs: int = 3
    """Number of past conversation runs to include for the leader."""

    # Iteration control
    max_iterations: int = 20
    """Maximum number of delegation iterations to prevent infinite loops."""

    # Member message filter
    member_message_filter: Optional[Union[TeamMemberMessageFilter, Dict[str, TeamMemberMessageFilter]]] = None
    """Optional message filter for member responses.

    Can be either:
    1. A single TeamMemberMessageFilter function that applies to all members
    2. A Dict[str, TeamMemberMessageFilter] where keys are member names for per-member filtering

    When set, the filter is applied to all Content objects collected from member
    execution to produce the response text for delegation records.

    If not specified, defaults to keep_all_member_message (current behavior).

    Built-in filters:
    - keep_all_member_message: Returns all text from all messages (default)
    - keep_last_member_message: Returns only text from the last message

    Example (single filter for all members):
        from trpc_agent_sdk.teams import keep_last_member_message

        team = TeamAgent(
            name="my_team",
            members=[researcher, writer],
            member_message_filter=keep_last_member_message,
        )

    Example (per-member filters):
        from trpc_agent_sdk.teams import keep_all_member_message, keep_last_member_message

        team = TeamAgent(
            name="my_team",
            members=[researcher, writer],
            member_message_filter={
                "researcher": keep_all_member_message,
                "writer": keep_last_member_message,
            },
        )
    """

    # Private fields (not serialized)
    _leader_agent: Optional[LlmAgent] = None
    """Internal leader agent for delegation decisions."""

    _long_running_tool_names: set = None
    """Set of tool names that are LongRunningFunctionTool (for HITL filtering)."""

    def __setattr__(self, name: str, value: Any) -> None:
        """Override setattr to sync parent_agent to leader_agent.

        When parent_agent is set (e.g., by LlmAgent adding TeamAgent as a sub_agent),
        this ensures the leader_agent also gets the parent reference and transfer permissions.
        """
        super().__setattr__(name, value)

        # When parent_agent is set, sync to leader_agent if it exists
        if name == 'parent_agent':
            self._leader_agent.parent_agent = value

    def model_post_init(self, __context: Any) -> None:
        """Post init hook for TeamAgent.

        Initializes the internal leader agent and syncs transfer hierarchy.
        """
        # Initialize leader agent
        self._init_leader_agent()

        # Sync leader's transfer hierarchy with TeamAgent's
        self._sync_leader_transfer_hierarchy()

        return super().model_post_init(__context)

    def _init_leader_agent(self) -> None:
        """Initialize the internal leader agent with delegation tools and custom tools."""
        # Create delegation tool for coordinate mode
        member_names = [m.name for m in self.members]
        delegation_tools = [create_delegate_to_member_tool(member_names)]

        # Combine delegation tools with team's custom tools
        all_leader_tools = delegation_tools + list(self.tools) if self.tools else delegation_tools

        # Collect LongRunningFunctionTool names for HITL filtering
        # These tools should be skipped when extracting text from events
        self._long_running_tool_names = set()
        if self.tools:
            for tool in self.tools:
                if isinstance(tool, LongRunningFunctionTool):
                    self._long_running_tool_names.add(tool.name)
                    logger.debug("TeamAgent: Registered long-running tool: %s", tool.name)

        # Determine leader instruction based on TeamAgent.instruction type
        leader_instruction = self._create_leader_instruction()

        # Create internal LlmAgent for team leader
        self._leader_agent = LlmAgent(
            name=f"{self.name}",
            model=self.model,
            instruction=leader_instruction,
            tools=all_leader_tools,
            skill_repository=self.skill_repository,
            add_name_to_instruction=False,  # We already have full instruction
            disable_react_tool=True,  # TeamAgent controls tool reaction externally
        )

    def _create_leader_instruction(self) -> Union[str, InstructionProvider]:
        """Create leader instruction based on TeamAgent.instruction type.

        If TeamAgent.instruction is a static string, generate the complete leader
        instruction immediately. If it's a callable (InstructionProvider), wrap it
        to add team-specific context dynamically.

        Returns:
            Union[str, InstructionProvider]: Static string or dynamic provider.
        """
        if isinstance(self.instruction, str):
            # Static instruction: generate complete leader instruction now
            member_info = get_member_info_list(self.members)
            return generate_team_leader_system_message(
                team_name=self.name,
                team_instruction=self.instruction,
                members=member_info,
            )
        else:
            # Dynamic instruction: return method that wraps user's instruction with team context
            return self._resolve_dynamic_leader_instruction

    async def _resolve_dynamic_leader_instruction(self, ctx: InvocationContext) -> str:
        """Resolve dynamic leader instruction at runtime.

        This method wraps the user-provided instruction callable and adds
        team-specific context (member info, delegation instructions).

        Args:
            ctx: The invocation context for dynamic instruction resolution.

        Returns:
            str: Complete leader instruction with team context.
        """
        # Resolve the dynamic team instruction
        team_instruction = self.instruction(ctx)
        if inspect.isawaitable(team_instruction):
            team_instruction = await team_instruction

        # Generate complete leader instruction with member info
        member_info = get_member_info_list(self.members)
        return generate_team_leader_system_message(
            team_name=self.name,
            team_instruction=team_instruction,
            members=member_info,
        )

    def _sync_leader_transfer_hierarchy(self) -> None:
        """Sync leader_agent's transfer hierarchy with TeamAgent's.

        This synchronizes the leader_agent's parent_agent reference and transfer
        flags (disallow_transfer_to_parent, disallow_transfer_to_peers) to match
        the TeamAgent's configuration, enabling proper transfer_to_agent capability.

        Called during model_post_init() to ensure the leader is properly configured
        before any execution occurs.
        """
        # Update leader's parent reference to match TeamAgent's parent transfer policy
        self._leader_agent.disallow_transfer_to_parent = self.disallow_transfer_to_parent
        self._leader_agent.disallow_transfer_to_peers = self.disallow_transfer_to_peers

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Core team execution with state-based context management.

        Flow:
        1. Load or create TeamRunContext from session.state
        2. Check for human-in-the-loop (HITL) resume
        3. Add initial user message to context if this is the first turn
        4. Main delegation loop:
           a. Build leader messages from TeamRunContext
           b. Run leader agent with override_messages
           c. Detect LongRunningEvent (HITL) - save state and exit
           d. Analyze last event:
              - If delegation: execute member, update state, continue loop
              - If finished: break loop
        5. Update session.state via state_delta after each significant action

        Human-in-the-loop Support:
        - When leader triggers a LongRunningEvent, TeamAgent saves pending state
          and exits, yielding the event to the Runner
        - On resume, if user provides FunctionResponse matching the pending ID,
          TeamAgent passes it directly to the leader (not as text-only messages)
        - The leader processes the FunctionResponse and continues execution

        Args:
            ctx: The invocation context containing session, services, etc.

        Yields:
            Event: Team output events including leader and member responses.
        """
        # 1. Detect member mode: override_messages indicates external control (e.g., parent TeamAgent)
        is_member_mode = ctx.override_messages is not None

        # 2. Initialize message builder with all history options
        message_builder = TeamMessageBuilder(
            share_team_history=self.share_team_history,
            num_team_history_runs=self.num_team_history_runs,
            share_member_interactions=self.share_member_interactions,
            num_member_history_runs=self.num_member_history_runs,
            add_history_to_leader=self.add_history_to_leader,
            num_leader_history_runs=self.num_history_runs,
        )

        # 3. Load or create TeamRunContext based on mode
        if is_member_mode:
            # Member mode: Use ephemeral context (don't touch session.state)
            team_run_context = TeamRunContext(team_name=self.name)
            logger.debug("TeamAgent '%s' running in member mode with ephemeral context", self.name)
        else:
            # Root mode: Load from session.state (current behavior)
            team_run_context = TeamRunContext.from_state(
                state=ctx.session.state,
                team_name=self.name,
            )
            logger.debug("TeamAgent '%s' running in root mode, loaded context: %s", self.name, team_run_context)

        # Set current invocation ID for tracking run boundaries
        team_run_context.current_invocation_id = ctx.invocation_id

        # Create lock for thread-safe access to team_run_context in parallel execution
        context_lock = asyncio.Lock()

        # Track current activity for cancellation context
        current_activity = "initialization"

        # 4. Handle initial input based on mode
        user_text = ""
        if is_member_mode:
            # Member mode: Extract task from override_messages
            user_text = self._extract_text_from_override_messages(ctx.override_messages)
            if user_text:
                team_run_context.add_leader_message('user', user_text)
        elif team_run_context.has_pending_hitl() and ctx.user_content:
            # Root mode with HITL resume
            function_response = self._extract_function_response_from_content(ctx.user_content)
            if function_response and function_response.id == team_run_context.pending_function_call_id:
                logger.debug("TeamAgent: Resuming from HITL, function_call.id=%s", function_response.id)
                # Extract text from FunctionResponse and add to leader_history
                user_text = self._extract_text_from_function_response(function_response)
                team_run_context.add_leader_message('user', user_text)
                # Clear pending state - we're resuming
                team_run_context.clear_pending_hitl()
                # Persist cleared state (root mode only)
                yield self._create_state_update_event(ctx, team_run_context)
        elif ctx.user_content:
            # Root mode: Normal flow
            user_text = self._extract_text_from_content(ctx.user_content)
            team_run_context.add_leader_message('user', user_text)

        # 5. Main delegation loop
        iteration = 0

        # Track leader's streaming text for cancellation handling
        leader_temp_text = ""

        try:
            while iteration < self.max_iterations:
                iteration += 1
                last_event: Optional[Event] = None

                # Update activity tracking
                current_activity = "leader planning"

                # Reset leader temp text at start of each iteration
                leader_temp_text = ""

                # Always build leader context with override_messages from TeamRunContext
                # Only include user_content on first iteration; after that it's already in history
                leader_messages = message_builder.build_leader_messages(team_run_context=team_run_context)
                logger.debug("Running leader agent (iteration %s) with messages: %s", iteration, leader_messages)
                leader_ctx = ctx.model_copy(update={"override_messages": leader_messages})

                # Collect leader's text response for state update
                leader_text_response = ""

                # Run leader agent and yield ALL events to Runner
                async for event in self._leader_agent.run_async(leader_ctx):
                    # Detect LongRunningEvent (human-in-the-loop triggered by leader)
                    if isinstance(event, LongRunningEvent):
                        logger.debug("TeamAgent: Leader triggered LongRunningEvent, function_call.id=%s",
                                     event.function_call.id)

                        if is_member_mode:
                            # Member mode: HITL is NOT allowed, raise exception
                            raise RuntimeError(
                                f"TeamAgent '{self.name}' is running in member mode and cannot trigger "
                                f"Human-In-The-Loop (LongRunningEvent). Tool '{event.function_call.name}' "
                                f"requires human input which is not supported for nested TeamAgents.")

                        # Root mode: Save pending HITL state
                        team_run_context.set_pending_hitl(event.function_call.id)
                        # Persist state before exiting
                        yield self._create_state_update_event(ctx, team_run_context)

                        # Update the event's author to TeamAgent's name so Runner can find us
                        # The leader agent is internal and not in the agent tree
                        event.author = self.name
                        yield event

                        # Continue will normally exit the _leader_agent.run_async
                        continue

                    yield event  # Yield normal events to Runner for storage

                    # Track leader's streaming text (partial events)
                    if event.partial and event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.text:
                                leader_temp_text += part.text

                    # LongRunningEvent is partial, so will not enter below logic.
                    if not event.partial:
                        last_event = event
                        # Collect text from leader's response
                        event_text = self._extract_text_from_event(event)
                        if event_text:
                            leader_text_response += event_text
                        # Reset temp text when we get a complete message
                        leader_temp_text = ""

                # Analyze last event to decide next action
                if last_event is None:
                    # No events produced, finish
                    logger.debug("TeamAgent: No events from leader, finishing")
                    break

                # Detect transfer agent request, return and let runner transfer
                transfer_to_agent = last_event.actions.transfer_to_agent
                if transfer_to_agent:
                    logger.debug("TeamAgent: Leader requested transfer to: %s", transfer_to_agent)
                    return

                # Try to extract delegation signals from last event
                signals = self._extract_delegation_signals(last_event)

                if signals:
                    # Delegation detected, execute member agent(s)
                    logger.debug("TeamAgent: Found %s delegation signal(s)", len(signals))

                    # Execute delegations based on parallel_execution flag
                    if self.parallel_execution and len(signals) > 1:
                        # Update activity tracking for parallel execution
                        member_names = [s.member_name for s in signals]
                        current_activity = f"delegation to {', '.join(member_names)}"

                        # Parallel execution for multiple signals
                        async for event in self._execute_delegations_parallel(ctx, signals, team_run_context,
                                                                              message_builder, is_member_mode,
                                                                              context_lock):
                            yield event
                    else:
                        # Sequential execution
                        for signal in signals:
                            # Update activity tracking for sequential execution
                            current_activity = f"delegation to {signal.member_name}"

                            logger.debug("TeamAgent: Delegating to %s with task: %s...", signal.member_name or 'all',
                                         signal.task[:100])

                            # _execute_delegation handles interaction recording internally
                            async for member_event in self._execute_delegation(ctx, signal, team_run_context,
                                                                               message_builder, is_member_mode,
                                                                               context_lock):
                                yield member_event  # Yield member events to Runner

                    # Persist updated state (root mode only)
                    if not is_member_mode:
                        yield self._create_state_update_event(ctx, team_run_context)

                    # Continue loop to run leader again with member response
                    continue

                # Check if custom tools were executed (non-delegation)
                custom_tool_executed = self._has_non_delegation_tool_calls(last_event)
                if custom_tool_executed:
                    # Custom tool was executed, continue loop to let leader process result
                    logger.debug("TeamAgent: Custom tool executed, continuing loop for leader to process result")

                    # Add leader's response (including tool calls as text) to history
                    if leader_text_response.strip():
                        team_run_context.add_leader_message('model', leader_text_response)

                    # Persist state update (root mode only)
                    if not is_member_mode:
                        yield self._create_state_update_event(ctx, team_run_context)
                    continue

                # No delegation and no custom tools - leader finished with text response
                if leader_text_response.strip():
                    team_run_context.add_leader_message('model', leader_text_response)
                    if not is_member_mode:
                        yield self._create_state_update_event(ctx, team_run_context)

                logger.debug("TeamAgent: Leader finished without delegation")
                break

            if iteration >= self.max_iterations:
                logger.warning("TeamAgent: Reached max iterations (%s)", self.max_iterations)

        except RunCancelledException:
            # Handle cancellation: add record to team memory, persist state, re-raise
            logger.info("TeamAgent '%s' cancelled during %s", self.name, current_activity)

            # If cancelled during leader planning and we have partial text, save it
            if current_activity == "leader planning" and leader_temp_text:
                partial_leader_response = f"{leader_temp_text}\n[Response interrupted by cancellation]"
                team_run_context.add_leader_message('model', partial_leader_response)

            # Add cancellation record to team memory (keep partial delegation records)
            team_run_context.add_cancellation_record(cancelled_during=current_activity)

            # Persist updated state (root mode only)
            if not is_member_mode:
                yield self._create_state_update_event(ctx, team_run_context)

            # Re-raise to let Runner handle session cleanup
            raise

    def _extract_delegation_signals(self, event: Event) -> List[DelegationSignal]:
        """Extract ALL DelegationSignals from event's function responses.

        The delegation tool returns a DelegationSignal Pydantic model. Due to
        FunctionTool's serialization (model_dump_json()), the signal becomes a JSON
        string wrapped by ToolsProcessor as {"result": "<json_string>"}.

        This method handles multiple formats:
        1. DelegationSignal instance - direct use (rare, if serialization skipped)
        2. dict with marker field - parse directly
        3. JSON string with marker - parse JSON then create signal

        In coordinate mode, the leader may call delegate_to_member multiple times
        in a single turn, resulting in multiple function_response parts.

        Args:
            event: Event to check for delegation signals.

        Returns:
            List of DelegationSignals found (empty list if none).
        """
        signals: List[DelegationSignal] = []
        function_responses = event.get_function_responses()

        for response in function_responses:
            try:
                response_data = response.response
                if not isinstance(response_data, dict):
                    continue

                # ToolsProcessor wraps non-dict results as {"result": result}
                result = response_data.get("result")

                # Check if result is already a DelegationSignal instance
                if isinstance(result, DelegationSignal):
                    signals.append(result)
                    continue

                # Check if result is a delegation signal (handles dict and JSON string)
                if DelegationSignal.is_delegation_signal(result):
                    signal = DelegationSignal.from_response(result)
                    if signal:
                        signals.append(signal)
                    continue

                # Also check top-level for backwards compatibility
                # (in case the tool returns a dict directly)
                if DelegationSignal.is_delegation_signal(response_data):
                    signal = DelegationSignal.from_response(response_data)
                    if signal:
                        signals.append(signal)

            except (AttributeError, TypeError):
                continue

        return signals

    def _create_state_update_event(
        self,
        ctx: InvocationContext,
        team_run_context: TeamRunContext,
    ) -> Event:
        """Create an event that updates session.state with TeamRunContext.

        This event uses state_delta to persist the TeamRunContext to session.state.

        Args:
            ctx: The invocation context.
            team_run_context: The context to save.

        Returns:
            Event with state_delta set to update session.state.
        """
        event = Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            content=None,  # No content, just state update
            branch=ctx.branch,
            partial=False,
        )
        event.actions.state_delta = team_run_context.get_state_delta()
        return event

    async def _execute_delegation(
        self,
        ctx: InvocationContext,
        signal: DelegationSignal,
        team_run_context: TeamRunContext,
        message_builder: TeamMessageBuilder,
        is_member_mode: bool = False,
        context_lock: Optional[asyncio.Lock] = None,
    ) -> AsyncGenerator[Event, None]:
        """Execute delegation based on parsed signal.

        Member agents handle their own tool reaction loop internally.
        TeamAgent just delegates the task and collects the final results.

        If cancelled during member execution, this method records partial
        response to team_run_context before re-raising the exception.

        Args:
            ctx: The invocation context.
            signal: The delegation signal with action and task.
            team_run_context: Runtime context for tracking interactions.
            message_builder: Builder for member messages.
            is_member_mode: Whether TeamAgent is running in member mode.
            context_lock: Optional lock for thread-safe context access in parallel mode.

        Yields:
            Events from member execution.
        """
        member_name = signal.member_name
        task = signal.task
        member = self._find_member_by_name(member_name)
        if not member:
            error_msg = f"Error: Member '{member_name}' not found. Available: {[m.name for m in self.members]}"
            logger.error("TeamAgent: %s", error_msg)
            # Yield error as text event
            yield Event(
                invocation_id=ctx.invocation_id,
                author=self.name,
                content=Content(role="model", parts=[Part.from_text(text=error_msg)]),
                branch=ctx.branch,
            )
            team_run_context.add_interaction(member_name, task, error_msg)
            return

        # Build initial messages for member
        messages = message_builder.build_member_messages(
            task=task,
            team_run_context=team_run_context,
            member_name=member_name,
        )

        # Create context with override_messages
        logger.debug("Execute member %s with messages %s", member_name, messages)
        member_ctx = ctx.model_copy(update={"override_messages": messages})

        # Collect Content objects from member execution for filtering
        collected_contents: List[Content] = []

        # Track streaming text for cancellation handling
        member_temp_text = ""

        try:
            # Execute member via run_async - member handles its own tool reaction internally
            async for event in member.run_async(member_ctx):
                # Block HITL from member agents when in member mode
                if is_member_mode and isinstance(event, LongRunningEvent):
                    raise RuntimeError(f"TeamAgent '{self.name}' is running in member mode. Member agent "
                                       f"'{member_name}' cannot trigger Human-In-The-Loop (LongRunningEvent). "
                                       f"Tool '{event.function_call.name}' requires human input which is not "
                                       f"supported for nested TeamAgents.")

                yield event  # Yield member events to Runner for storage

                # Track streaming text (partial events with text content)
                if event.partial and event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text:
                            member_temp_text += part.text

                # Process non-partial events with content
                if event.content and not event.partial:
                    collected_contents.append(event.content)
                    # Reset temp_text when we get a complete message
                    member_temp_text = ""

        except RunCancelledException:
            # Record partial response if we have any streaming text
            if member_temp_text:
                partial_response = f"{member_temp_text}\n[Response interrupted by cancellation]"
            else:
                partial_response = "[Response interrupted by cancellation before any output]"

            # Record the partial interaction
            if context_lock:
                async with context_lock:
                    team_run_context.add_interaction(member_name, task, partial_response)
                    team_run_context.add_delegation_record(member_name, task, partial_response)
            else:
                team_run_context.add_interaction(member_name, task, partial_response)
                team_run_context.add_delegation_record(member_name, task, partial_response)

            logger.debug("TeamAgent: Member '%s' cancelled with partial response: %s...", member_name,
                         partial_response[:100])

            # Re-raise to propagate cancellation
            raise

        # Apply message filter to get response text
        # print(f"Got collected_contents: {collected_contents}")
        response_text = await self._apply_member_message_filter(member_name, collected_contents)

        # Record interaction for potential sharing with other members
        # Use lock if provided (parallel execution mode)
        if context_lock:
            async with context_lock:
                team_run_context.add_interaction(member_name, task, response_text)
                team_run_context.add_delegation_record(member_name, task, response_text)
        else:
            team_run_context.add_interaction(member_name, task, response_text)
            team_run_context.add_delegation_record(member_name, task, response_text)
        logger.debug("TeamAgent: Member '%s' completed task, response length: %s", member_name, len(response_text))

    async def _execute_delegations_parallel(
        self,
        ctx: InvocationContext,
        signals: List[DelegationSignal],
        team_run_context: TeamRunContext,
        message_builder: TeamMessageBuilder,
        is_member_mode: bool = False,
        context_lock: Optional[asyncio.Lock] = None,
    ) -> AsyncGenerator[Event, None]:
        """Execute multiple delegations in parallel.

        Used when parallel_execution=True and there are multiple delegation signals.
        Each _execute_delegation call handles its own interaction recording.

        Args:
            ctx: The invocation context.
            signals: List of delegation signals to execute.
            team_run_context: Runtime context for tracking interactions.
            message_builder: Builder for member messages.
            is_member_mode: Whether TeamAgent is running in member mode.
            context_lock: Lock for thread-safe access to team_run_context.

        Yields:
            Events from all member executions.
        """

        async def run_delegation(signal: DelegationSignal) -> List[Event]:
            """Run a single delegation and collect events."""
            events: List[Event] = []
            async for event in self._execute_delegation(ctx, signal, team_run_context, message_builder, is_member_mode,
                                                        context_lock):
                events.append(event)
            return events

        # Run all delegations in parallel
        logger.debug("TeamAgent: Executing %s delegations in parallel", len(signals))
        results = await asyncio.gather(*[run_delegation(signal) for signal in signals])

        # Yield all events (interaction records already added by _execute_delegation)
        for events in results:
            for event in events:
                yield event

    def _find_member_by_name(self, name: str) -> Optional[BaseAgent]:
        """Find a member agent by name.

        Args:
            name: Name of the member to find.

        Returns:
            BaseAgent if found, None otherwise.
        """
        for member in self.members:
            if member.name == name:
                return member
        return None

    def _has_non_delegation_tool_calls(self, event: Event) -> bool:
        """Check if event contains non-delegation and non-long-running tool calls.

        Returns True if the event has function_call or function_response
        for tools that are NOT delegation tools and NOT long-running tools.

        Long-running tools (HITL) are handled separately via LongRunningEvent
        detection, so they should not trigger the "custom tool executed" path.

        Args:
            event: Event to check for regular tool usage.

        Returns:
            True if regular (non-delegation, non-HITL) tools were called, False otherwise.
        """
        if not event or not event.content or not event.content.parts:
            return False

        # Tools to skip: delegation tools + long-running tools (HITL)
        tools_to_skip = {DELEGATE_TOOL_NAME}
        if self._long_running_tool_names:
            tools_to_skip.update(self._long_running_tool_names)

        for part in event.content.parts:
            if part.function_call:
                if part.function_call.name not in tools_to_skip:
                    return True
            if part.function_response:
                if part.function_response.name not in tools_to_skip:
                    return True

        return False

    def _extract_text_from_event(self, event: Event) -> str:
        """Extract text content from an event.

        Converts all content (text, function_call, function_response) to text format.
        Delegation tools are always skipped.
        Long-running tools: function_call is kept (as text), function_response is skipped.

        Args:
            event: Event to extract text from.

        Returns:
            Extracted text string.
        """
        if not event or not event.content or not event.content.parts:
            return ""

        # Tools to skip completely (both function_call and function_response)
        delegation_tools = {DELEGATE_TOOL_NAME}

        texts = []
        for part in event.content.parts:
            # Skip thinking content
            if part.thought:
                continue

            if part.text:
                texts.append(part.text)
            elif part.function_call:
                fc = part.function_call
                # Skip delegation tools completely
                if fc.name in delegation_tools:
                    continue
                # Keep long-running function_call as text (for HITL tracking)
                texts.append(f"[Tool Call: {fc.name}({fc.args})]")
            elif part.function_response:
                fr = part.function_response
                # Skip delegation tool responses
                if fr.name in delegation_tools:
                    continue
                # Skip long-running tool responses (they are pending placeholders)
                if self._long_running_tool_names and fr.name in self._long_running_tool_names:
                    continue
                texts.append(f"[Tool Result: {fr.response}]")

        return "\n".join(texts) if texts else ""

    def _extract_text_from_content(self, content: Content) -> str:
        """Extract text content from a Content object.

        Only extracts text parts, skipping:
        - Thought content (part.thought=True)
        - Function calls (part.function_call)
        - Function responses (part.function_response)

        Args:
            content: Content to extract text from.

        Returns:
            Extracted text string.
        """
        if not content or not content.parts:
            return ""
        texts = []
        for part in content.parts:
            # Only extract text parts, skip thoughts
            if part.text and not part.thought:
                texts.append(part.text)
        return " ".join(texts)

    def _extract_text_from_override_messages(self, override_messages: List[Content]) -> str:
        """Extract task text from override_messages (member mode).

        When TeamAgent runs as a member, the parent provides the task
        via override_messages. This extracts the text content.

        Args:
            override_messages: Messages provided by parent TeamAgent.

        Returns:
            Extracted text representing the task.
        """
        if not override_messages:
            return ""

        texts = []
        for content in override_messages:
            text = self._extract_text_from_content(content)
            if text:
                texts.append(text)

        return "\n".join(texts)

    async def _apply_member_message_filter(self, member_name: str, contents: List[Content]) -> str:
        """Apply member message filter to collected contents.

        Args:
            member_name: Name of the member agent (used to look up per-member filter).
            contents: List of Content objects from member execution.

        Returns:
            Filtered text string for the delegation record.
        """
        # Determine which filter to use
        filter_func = None

        if self.member_message_filter is None:
            # No filter configured, use default
            filter_func = keep_all_member_message
        elif isinstance(self.member_message_filter, dict):
            # Per-member filter dict: look up by member name, fall back to default
            filter_func = self.member_message_filter.get(member_name, keep_all_member_message)
        else:
            # Single filter function for all members
            filter_func = self.member_message_filter

        # Call the filter function (may be sync or async)
        result = filter_func(contents)
        if inspect.isawaitable(result):
            result = await result

        return result

    # Human-in-the-loop (HITL) helper methods
    def _extract_function_response_from_content(self, content: Content) -> Optional[FunctionResponse]:
        """Extract FunctionResponse from user content if present.

        Used to detect when user provides human-in-the-loop input.

        Args:
            content: User content that may contain a FunctionResponse.

        Returns:
            FunctionResponse if found, None otherwise.
        """
        if not content or not content.parts:
            return None
        for part in content.parts:
            if part.function_response:
                return part.function_response
        return None

    def _extract_text_from_function_response(self, function_response: FunctionResponse) -> str:
        """Extract text representation from a FunctionResponse for HITL resume.

        Converts the human-provided FunctionResponse to a text format that can be
        added to leader_history. This allows the leader to see the human's response
        in a readable format.

        Args:
            function_response: The FunctionResponse from human input.

        Returns:
            Text representation of the function response.
        """
        response_data = function_response.response
        tool_name = function_response.name

        # Format the response as readable text
        if isinstance(response_data, dict):
            # Format dict as key-value pairs
            parts = [f"[Human Response for {tool_name}]:"]
            for key, value in response_data.items():
                parts.append(f"  {key}: {value}")
            return "\n".join(parts)
        else:
            # Simple string or other type
            return f"[Human Response for {tool_name}]: {response_data}"
