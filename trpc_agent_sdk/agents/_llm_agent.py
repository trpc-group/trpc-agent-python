# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""LLM Agent implementation for TRPC Agent framework.

This module provides the LlmAgent class which extends BaseAgent to provide
LLM-powered conversational capabilities. It integrates with the model system,
filter framework, and session management to deliver AI agent functionality.
"""

from __future__ import annotations

from typing import Any
from typing import AsyncGenerator
from typing import Awaitable
from typing import Callable
from typing import List
from typing import Literal
from typing import Optional
from typing import TypeAlias
from typing import Union
from typing_extensions import override

from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator

from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import ModelRegistry
from trpc_agent_sdk.planners import BasePlanner
from trpc_agent_sdk.skills import BaseSkillRepository
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import BaseToolSet
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools import LongRunningFunctionTool
from trpc_agent_sdk.tools import transfer_to_agent
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentConfig

from ..exceptions import RunCancelledException
from ._base_agent import BaseAgent
from ._callback import ModelCallback
from ._callback import ToolCallback
from .core import BranchFilterMode
from .core import CodeExecutionRequestProcessor
from .core import CodeExecutionResponseProcessor
from .core import LlmProcessor
from .core import TimelineFilterMode
from .core import ToolsProcessor
from .core import create_final_model_response_event
from .core import default_request_processor
from .core import get_structured_model_response

# Type aliases for instruction providers
InstructionProvider: TypeAlias = Callable[[InvocationContext], Union[str, Awaitable[str]]]

# Type aliases for tool definitions
ToolUnion: TypeAlias = Union[BaseTool, BaseToolSet]


class LlmAgent(BaseAgent):
    """LLM-based Agent for TRPC framework.

    This agent provides conversational AI capabilities by integrating with
    language models through the model registry system. It supports:
    - Model configuration and selection
    - Instruction and prompt management
    - Tool integration (future enhancement)
    - Filter-based processing pipeline
    - Session and context management
    """

    model: Union[str, LLMModel, Callable[[dict[str, Any]], Awaitable[LLMModel]]] = ""
    """The model to use for the agent.

    Can be either:
    - A model name string (resolved via registry)
    - A model instance (LLMModel)
    - An async factory callback that creates a model per-request

    When not set, the agent will inherit the model from its ancestor.
    """

    instruction: Union[str, InstructionProvider] = ""
    """Instructions for the LLM model, guiding the agent's behavior.

    Can be a static string or a callable that takes InvocationContext and returns
    instructions. The callable can be async.
    """

    tools: List[ToolUnion] = Field(default_factory=list)
    """Tools available to this agent.

    Can be a list of:
    - Callable functions (will be wrapped in FunctionTool)
    - BaseTool instances (used directly)
    - BaseToolSet instances (will be expanded to individual tools)
    """

    parallel_tool_calls: bool = False
    """Parallel tool call"""

    generate_content_config: Optional[GenerateContentConfig] = None
    """The additional content generation configurations.

    NOTE: not all fields are usable, e.g. tools must be configured via `tools`,
    thinking_config must be configured via `planner` in LlmAgent.

    For example: use this config to adjust model temperature, configure safety
    settings, etc.
    """

    include_contents: Literal['default', 'none'] = 'default'
    """Controls content inclusion in model requests.

    Options:
        default: Model receives relevant conversation history
        none: Model receives no prior history, operates solely on current
            instruction and input
    """

    include_previous_history: bool = True
    """Controls whether previous agent history is included in model requests.

    When True (default), previous agent outputs are converted to user context
    and included in the conversation history. When False, previous agent outputs
    are excluded from history, only keeping user messages and current agent's
    own history.
    """

    max_history_messages: int = 0
    """Maximum number of history messages to include in model requests.

    When set to 0 (default), no limit is applied and all filtered messages
    are included. When set to a positive value, only the most recent N messages
    (after all other filtering) will be included. This is useful for controlling
    token usage in long conversations.

    Note: This limit is applied AFTER timeline and branch filtering.
    """

    message_timeline_filter_mode: TimelineFilterMode = TimelineFilterMode.ALL
    """Set the filter mode for messages passed to the model (timeline dimension).

    The final messages passed to the model must satisfy both message_timeline_filter_mode
    and message_branch_filter_mode conditions.

    Timeline dimension filter conditions:
    Default: TimelineFilterMode.ALL

    Optional values:
      - TimelineFilterMode.ALL: Includes historical messages as well as messages
        generated in the current invocation (runner.run_async() call)
      - TimelineFilterMode.INVOCATION: Only includes messages generated in the
        current invocation (runner.run_async() call)
    """

    message_branch_filter_mode: BranchFilterMode = BranchFilterMode.ALL
    """Set the filter mode for messages passed to the model (branch dimension).

    The final messages passed to the model must satisfy both message_timeline_filter_mode
    and message_branch_filter_mode conditions.

    Branch dimension filter conditions:
    Default: BranchFilterMode.ALL

    Optional values:
      - BranchFilterMode.ALL: Includes messages from all agents. Use this when the
        current agent interacts with the model and needs to synchronize all valid
        content messages generated by all agents to the model.
      - BranchFilterMode.PREFIX: Filters messages by prefix matching Event.branch
        with Invocation.branch. Use this when you want to pass messages generated
        by the current agent and related upstream/downstream agents to the model.
      - BranchFilterMode.EXACT: Filters messages where Event.branch == Invocation.branch.
        Use this when the current agent interacts with the model and only needs to
        use messages generated by the current agent.
    """

    input_schema: Optional[type[BaseModel]] = None
    """The input schema when agent is used as a tool.

    When set, the agent expects structured input matching this Pydantic model.
    This is used when the agent is called as a tool by another agent.
    """

    output_schema: Optional[type[BaseModel]] = None
    """The output schema for structured responses.

    When set, the agent will provide structured output matching this Pydantic model.
    This enables type-safe, structured responses from the agent.

    NOTE: When output_schema is set alongside tools, the agent will use the
    SetModelResponseTool to provide structured output while still being able
    to use other tools.
    """

    output_key: Optional[str] = None
    """Key in session state to store agent output for later use."""

    planner: Optional[BasePlanner] = None
    """Instructs the agent to make a plan and execute it step by step.

    This allows the agent to structure its thinking and reasoning process
    before taking actions. Available planners:
    - PlanReActPlanner: Enforces structured Plan-Reasoning-Action workflow
    - BuiltInPlanner: Uses model's built-in thinking features

    NOTE: To use model's built-in thinking features, set the `thinking_config`
    field in `BuiltInPlanner`.
    """
    skill_repository: Optional[BaseSkillRepository] = None
    """The skill repository to use for the agent.

    When set, the agent will use the skill repository to load skills.

    NOTE: When skill_repository is set, the agent will use the skill repository to load skills.
    The skill repository will be used to load skills for the agent.
    The skill repository will be used to load skills for the agent.
    """

    before_model_callback: Optional[ModelCallback] = None
    """Callback before model is called."""

    after_model_callback: Optional[ModelCallback] = None
    """Callback after model is called."""

    before_tool_callback: Optional[ToolCallback] = None
    """Callback before tool is called."""

    after_tool_callback: Optional[ToolCallback] = None
    """Callback after tool is called."""

    add_name_to_instruction: bool = True
    """Controls whether agent name is added to instruction.

    When True (default), the framework will inject 'You are an agent who's name is [agent_name].'
    into the instruction. When False, this injection is disabled, giving full control over the
    instruction content.
    """

    disable_react_tool: bool = False
    """When True, the agent returns after tool execution instead of continuing the multi-turn loop.

    This is useful when the agent is controlled by an external orchestrator (like TeamAgent)
    that wants to handle tool results externally. The orchestrator is responsible for
    continuing the conversation with the tool results.
    """

    default_transfer_message: Optional[str] = None
    """Controls whether default transfer instructions are added.

    When None (default), the framework will automatically inject transfer instructions via
    '_build_transfer_instructions' when agent transfer is enabled. When set to an empty string
    or custom message, the default transfer instruction injection is disabled.

    Note: Setting this to an empty string "" completely disables the default transfer message.
    Setting it to a custom string will use that string instead of the default message.
    """

    def _get_effective_branch_filter_mode(self) -> BranchFilterMode:
        """Get the effective branch filter mode, considering backward compatibility.

        This method provides backward compatibility for include_previous_history:
        - If message_branch_filter_mode is explicitly set (not default ALL), use it
        - Otherwise, derive from include_previous_history:
          - include_previous_history=True -> BranchFilterMode.ALL
          - include_previous_history=False -> BranchFilterMode.EXACT

        Returns:
            BranchFilterMode: The effective branch filter mode to use
        """
        # Check if include_previous_history was explicitly set by checking if it differs from default
        # Since we can't directly detect if a field was set, we use a heuristic:
        # If message_branch_filter_mode is not ALL, it was explicitly set, so use it
        # Otherwise, derive from include_previous_history for backward compatibility

        # If message_branch_filter_mode was explicitly changed from default, use it
        if self.message_branch_filter_mode != BranchFilterMode.ALL:
            return self.message_branch_filter_mode

        # Otherwise, derive from include_previous_history for backward compatibility
        if not self.include_previous_history:
            return BranchFilterMode.EXACT
        else:
            return BranchFilterMode.ALL

    @override
    def model_post_init(self, __context: Any) -> None:
        """Post init hook for agent."""
        # Skip initialization for factory callbacks - they're resolved per-request
        if callable(self.model):
            return super().model_post_init(__context)

        # Resolve string models via registry
        if not isinstance(self.model, LLMModel):
            self.model = ModelRegistry.create_model(self.model)

        return super().model_post_init(__context)

    @property
    def _tools_processor(self) -> ToolsProcessor:
        """Get the private tools processor instance built from the tools.

        This is a computed field that creates the ToolsProcessor with the agent's raw tools.
        The ToolsProcessor will handle BaseToolSet resolution by calling their get_tools()
        method during process_llm_request().

        Returns:
            ToolsProcessor: The tools processor instance that handles ToolUnion resolution

        Note:
            Tool resolution (including BaseToolSet.get_tools() calls) happens asynchronously
            in the ToolsProcessor.process_llm_request() method.
        """
        return ToolsProcessor(self.tools)

    def _get_extended_tools_processor(self, ctx: InvocationContext) -> ToolsProcessor:
        """Get a tools processor with extended tools including transfer tool and output schema tool if needed.

        This method creates a ToolsProcessor that includes both the agent's original tools
        and any additional tools needed for the current context (like transfer tool and set_model_response tool).

        Args:
            ctx: The invocation context

        Returns:
            ToolsProcessor: Extended tools processor instance
        """
        # Start with agent's original tools
        extended_tools = self.tools.copy() if self.tools else []

        # Add transfer tool if agent transfer should be enabled
        if self._should_enable_agent_transfer():
            try:
                transfer_tool = FunctionTool(transfer_to_agent)
                extended_tools.append(transfer_tool)
                logger.debug("Added transfer_to_agent tool to extended tools processor for agent: %s", self.name)
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Failed to add transfer tool to extended processor for agent %s: %s", self.name, ex)

        return ToolsProcessor(extended_tools)

    def _should_enable_agent_transfer(self) -> bool:
        """Determine if agent transfer should be enabled for this agent.

        Agent transfer is enabled when the agent has potential transfer targets:
        - Has sub-agents
        - Has parent agent and transfer to parent is allowed
        - Has peer agents and transfer to peers is allowed

        Returns:
            bool: True if agent transfer should be enabled
        """
        # Check if agent has sub-agents
        if self.sub_agents:
            return True

        # Check if transfer to parent is possible
        if self.parent_agent and not self.disallow_transfer_to_parent and self._is_llm_agent(self.parent_agent):
            return True

        # Check if transfer to peers is possible
        if (not self.disallow_transfer_to_peers and self.parent_agent and self._is_llm_agent(self.parent_agent)
                and len(self.parent_agent.sub_agents) > 1):  # Has siblings
            return True

        return False

    def _is_llm_agent(self, agent) -> bool:
        """Check if an agent is an LlmAgent (supports transfers)."""
        return isinstance(agent, LlmAgent)

    async def _resolve_model(self, ctx: InvocationContext) -> LLMModel:
        """Resolve model from string, instance, or factory callback.

        This method handles three types of model specifications:
        1. Factory callback: Invoked with custom_data from run_config
        2. String: Resolved via ModelRegistry
        3. LLMModel instance: Used directly

        For factory callbacks, filters are applied to the resolved model.

        Args:
            ctx: Invocation context with run_config.custom_data

        Returns:
            LLMModel: Resolved model instance ready for use
        """
        if callable(self.model):
            # Factory pattern - invoke with custom_data
            custom_data = ctx.run_config.custom_data if ctx.run_config else {}
            model = await self.model(custom_data)
            return model
        elif isinstance(self.model, str):
            # String pattern - resolve via registry
            return ModelRegistry.create_model(self.model)
        else:
            # Already an LLMModel instance
            return self.model

    def _create_error_event(self, ctx: InvocationContext, error_code: str, error_message: str) -> Event:
        """Create an error event with proper attribution.

        Args:
            ctx: The invocation context containing invocation information
            error_code: The error code for the event
            error_message: The error message for the event

        Returns:
            Event: Error event with proper attribution
        """
        return Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            error_code=error_code,
            error_message=error_message,
            branch=ctx.branch,
        )

    @override
    async def _run_async_impl(
        self,
        ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Core implementation of LLM agent execution.

        This method implements multi-turn conversation support with clean separation:
        1. Resolve model (may invoke factory callback)
        2. Call the LLM and collect all responses
        3. Collect any tool calls from the LLM response
        4. Execute tools if needed and store results in session
        5. Continue conversation loop until no more tool calls

        Args:
            ctx: The invocation context containing session, services, etc.
                 If ctx.override_messages is set, those messages are used as
                 conversation context instead of building from session.events.

        Yields:
            Event: Agent output events including responses, tool calls, and errors
        """

        # Resolve model (may invoke factory callback)
        model_instance = await self._resolve_model(ctx)
        llm_processor = LlmProcessor(model_instance)

        # Copy override_messages to local mutable list if provided (internal only)
        local_messages: Optional[List[Content]] = None
        if ctx.override_messages is not None:
            local_messages = list(ctx.override_messages)  # shallow copy

        def accumulate_content(event: Event) -> None:
            """Accumulate non-partial event content to local_messages for multi-turn support."""
            if local_messages is not None and not event.partial and event.content:
                local_messages.append(event.content)

        try:
            running = True
            # Multi-turn conversation loop - continue until no more tool calls or code execution
            while running:
                # CHECKPOINT 1: At start of each conversation turn
                await ctx.raise_if_cancelled()

                # Step 1: Build request using the request processor (includes conversation history)
                request = LlmRequest(model=model_instance.name, )

                error_event = await default_request_processor.build_request(
                    request,
                    self,
                    ctx,
                    override_messages=local_messages,
                )
                if error_event:
                    # Yield the error event directly
                    yield error_event
                    return

                # Step 1.5: Process code execution requests if code executor is configured
                if self.code_executor:
                    async for event in CodeExecutionRequestProcessor.run_async(ctx, request):
                        yield event

                # CHECKPOINT 2: Before LLM call
                await ctx.raise_if_cancelled()

                # Step 2: Call LLM and collect all responses
                collected_tool_calls = []
                code_was_executed = False

                logger.debug("Starting LLM call for agent: %s", self.name)

                # Use LlmProcessor to get unified events
                async for event in llm_processor.call_llm_async(request, ctx, stream=True):
                    # Handle different event types by checking content and error status
                    if event.is_error():
                        # Error event - yield and stop
                        yield event
                        return
                    elif event.content:
                        # Skip streaming tool calls (partial=True with streaming_tool_call metadata)
                        # These events are yielded directly for consumers to handle
                        if event.is_streaming_tool_call():
                            pass
                        else:
                            function_calls = event.get_function_calls()
                            if function_calls:
                                collected_tool_calls.extend(function_calls)
                                logger.debug("Collected %s tool calls from LLM", len(function_calls))

                        if event.is_final_response():
                            self._save_output_to_state(ctx, event)

                        # Process code execution responses if code executor is configured
                        if self.code_executor and event.content:
                            async for code_event in CodeExecutionResponseProcessor.run_async(ctx, event):
                                # Check if this is a code execution result event
                                if code_event.content and code_event.content.parts:
                                    for part in code_event.content.parts:
                                        if part.code_execution_result or part.executable_code:
                                            code_was_executed = True
                                            break
                                yield code_event

                        # Yield LLM response events directly
                        yield event
                        accumulate_content(event)
                    else:
                        # Yield other events directly
                        yield event
                    await ctx.raise_if_cancelled()

                # CHECKPOINT 4: Before tool execution
                await ctx.raise_if_cancelled()

                # Step 3: Execute tools if any were collected
                if collected_tool_calls:
                    logger.debug("Executing %s tool calls", len(collected_tool_calls))
                    logger.debug("Executing %s tool calls", len(collected_tool_calls))

                    try:
                        # Use extended tools processor that includes transfer tool if needed
                        extended_tools_processor = self._get_extended_tools_processor(ctx)

                        # Check if any of the tool calls are for long-running tools
                        long_running_tool_ids = set()
                        for tool_call in collected_tool_calls:
                            tool = await extended_tools_processor.find_tool(ctx, tool_call)
                            if tool and isinstance(tool, LongRunningFunctionTool):
                                long_running_tool_ids.add(tool_call.id)

                        # Execute tools and yield results (Runner will store them automatically)
                        last_tool_event = None
                        async for tool_event in extended_tools_processor.execute_tools_async(collected_tool_calls, ctx):
                            last_tool_event = tool_event

                            # Check if this event contains responses from long-running tools
                            if tool_event.content and tool_event.content.parts:
                                for part in tool_event.content.parts:
                                    if (part.function_response and part.function_response.id in long_running_tool_ids):
                                        # This is a response from a long-running tool
                                        # Find the corresponding function call
                                        corresponding_call = None
                                        for call in collected_tool_calls:
                                            if call.id == part.function_response.id:
                                                corresponding_call = call
                                                break

                                        if corresponding_call:
                                            # Import LongRunningEvent here to avoid circular imports
                                            from trpc_agent_sdk.events import LongRunningEvent

                                            # Create and yield LongRunningEvent
                                            long_running_event = LongRunningEvent(
                                                invocation_id=ctx.invocation_id,
                                                author=self.name,
                                                function_call=corresponding_call.model_copy(),
                                                function_response=part.function_response.model_copy(),
                                                branch=ctx.branch,
                                            )

                                            # Yield the regular tool event first
                                            yield tool_event

                                            # Then yield the long-running event
                                            yield long_running_event

                                            logger.debug("Long-running tool %s completed, yielding LongRunningEvent",
                                                         corresponding_call.name)
                                            return  # End agent execution after long-running event

                            # Yield regular tool events
                            yield tool_event
                            accumulate_content(tool_event)

                            # CHECKPOINT 5: During tool execution
                            await ctx.raise_if_cancelled()

                        # If set_model_response was executed, create and yield final model response event
                        if json_response := get_structured_model_response(last_tool_event):
                            final_event = create_final_model_response_event(ctx, json_response)
                            self._save_output_to_state(ctx, final_event)
                            yield final_event
                            logger.debug("set_model_response executed, ending agent execution")
                            return

                        # Check if any tool requested an agent transfer
                        if ctx.actions.transfer_to_agent:
                            # Clear the transfer action state
                            ctx.actions.transfer_to_agent = None
                            return  # End this agent's execution

                        # Check if tool reaction is disabled (external control mode)
                        # When disable_react_tool is True, external code (like TeamAgent)
                        # controls the conversation flow. Exit after tool execution to
                        # let the caller handle tool results and continue the loop.
                        if self.disable_react_tool:
                            logger.debug("disable_react_tool set, exiting after tool execution for external control")
                            return

                        # Continue the multi-turn loop for next LLM call with tool results in history
                        logger.debug("Tool execution completed, continuing conversation")
                        continue

                    except RunCancelledException as ex:
                        # raise to runner to handle
                        raise

                    except Exception as ex:  # pylint: disable=broad-except
                        logger.error("Error executing tools for agent %s: %s", self.name, ex, exc_info=True)

                        # Create tool execution error event without content
                        yield self._create_error_event(
                            ctx,
                            "tool_execution_failed",
                            f"Tool execution failed: {str(ex)}",
                        )
                        return

                # CHECKPOINT 6: After tool execution, before loop continuation
                await ctx.raise_if_cancelled()

                # Step 4: Check if code was executed and continue loop to let agent summarize results
                if code_was_executed:
                    logger.debug("Code execution completed, continuing conversation for agent to summarize results")
                    continue

                running = False
        except RunCancelledException as ex:
            # raise to runner to handle
            raise
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Unexpected error in LLM agent %s: %s", self.name, ex, exc_info=True)

            # Create agent execution error event without content
            yield self._create_error_event(
                ctx,
                "agent_execution_failed",
                f"Agent execution failed: {str(ex)}",
            )

    def _save_output_to_state(self, ctx: InvocationContext, event: Event) -> None:
        """Save agent output to session state if output_key is configured.

        Args:
            ctx: The invocation context
            content: The content to save
        """
        if self.output_key:
            # Save output to state using the delta tracking system
            result = ''.join([part.text if not part.thought else '' for part in event.content.parts])
            ctx.state[self.output_key] = result
            event.actions.state_delta[self.output_key] = result
            logger.debug("Saved agent output to state key '%s': %s...", self.output_key, result[:100])

    @field_validator('generate_content_config', mode='after')
    @classmethod
    def __validate_generate_content_config(
            cls, generate_content_config: Optional[GenerateContentConfig]) -> GenerateContentConfig:
        if not generate_content_config:
            return GenerateContentConfig()
        if generate_content_config.thinking_config:
            raise ValueError('Thinking config should be set via LlmAgent.planner.')
        if generate_content_config.tools:
            raise ValueError('All tools must be set via LlmAgent.tools.')
        if generate_content_config.system_instruction:
            raise ValueError('System instruction must be set via LlmAgent.instruction.')
        if generate_content_config.response_schema:
            raise ValueError('Response schema must be set via LlmAgent.output_schema.')
        return generate_content_config

    @field_validator('code_executor', mode='after')
    @classmethod
    def __validate_code_executor(cls, code_executor: Optional[BaseCodeExecutor]) -> Optional[BaseCodeExecutor]:
        """Validate code executor configuration."""
        if code_executor and not isinstance(code_executor, BaseCodeExecutor):
            raise ValueError('Code executor must be an instance of BaseCodeExecutor.')
        return code_executor


# Ensure forward references are resolved when this module is imported
# This handles cases where LlmAgent is imported directly without going through __init__.py
def _rebuild_models():
    """Rebuild Pydantic models to resolve forward references."""
    try:
        InvocationContext.model_rebuild()
        LlmAgent.model_rebuild()
    except Exception:  # pylint: disable=broad-except
        # Ignore rebuild errors during initial import
        pass


_rebuild_models()
