# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
"""Claude agent for TRPC Agent framework."""

import asyncio
import inspect
import re
import sys
from pathlib import Path
from typing import Any
from typing import AsyncGenerator
from typing import Awaitable
from typing import Callable
from typing import List
from typing import Optional
from typing import Set
from typing import Union
from typing_extensions import override

from claude_agent_sdk import AssistantMessage
from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk import ResultMessage
from claude_agent_sdk import SdkMcpTool
from claude_agent_sdk import SystemMessage
from claude_agent_sdk import TextBlock
from claude_agent_sdk import ThinkingBlock
from claude_agent_sdk import ToolResultBlock
from claude_agent_sdk import ToolUseBlock
from claude_agent_sdk import UserMessage
from claude_agent_sdk import create_sdk_mcp_server
from claude_agent_sdk.types import StreamEvent
from pydantic import ConfigDict
from pydantic import Field

from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.agents import InstructionProvider
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.exceptions import RunCancelledException
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import TOOL_STREAMING_ARGS
from trpc_agent_sdk.telemetry import CustomTraceReporter
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import BaseToolSet
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import Type

from ._runtime import AsyncRuntime
from ._session_config import SessionConfig
from ._session_manager import SessionManager
from ._setup import _add_model
from ._setup import _delete_model

# Type alias for tool definitions
ToolUnion = Union[BaseTool, BaseToolSet, Callable]


class ClaudeAgent(BaseAgent):
    """Claude Agent integration for TRPC Agent framework.

    This agent integrates Anthropic's Claude Code SDK with the TRPC Agent framework,
    enabling powerful agentic workflows with Claude models through a proxy server.

    Features:
        - Model configuration and automatic proxy registration
        - System instruction with template substitution
        - Tool integration via MCP servers (tools are automatically converted)
        - Session state management
        - Streaming support via ClaudeSDKClient

    Configuration:
        - claude_agent_options: ClaudeAgentOptions from claude_agent_sdk that will be merged
          with the agent's model, instruction, and tools properties
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, )
    """Pydantic model config."""

    model: Union[LLMModel, Callable[[dict[str, Any]], Awaitable[LLMModel]]]
    """Model to use. Must be an LLMModel instance or async factory callback.

    Can be either:
    - An LLMModel instance (static model)
    - An async factory callback that creates a model per-request

    The model will be automatically added to the proxy server and the returned
    model key will be used for Claude API calls.
    """

    instruction: Union[str, InstructionProvider] = ""
    """System instruction/prompt for Claude.

    Supports template substitution from session state using {variable} syntax.
    Example: "You are assisting {user_name} in {user_city}"

    Can also be a callable that accepts InvocationContext and returns a string.
    """

    tools: List[ToolUnion] = Field(default_factory=list)
    """Tools available to this agent.

    Can be a list of:
    - Callable functions (will be wrapped in FunctionTool)
    - BaseTool instances (used directly)
    - BaseToolSet instances (will be expanded to individual tools)

    Tools are automatically converted to Claude SDK MCP tools and registered with
    the proxy server.
    """

    claude_agent_options: Optional[ClaudeAgentOptions] = None
    """Optional ClaudeAgentOptions to configure the agent.

    These options will be merged with the agent's model, instruction, and tools properties.
    If not provided, default options will be used.
    """

    generate_content_config: Optional[GenerateContentConfig] = None
    """The additional content generation configurations.

    This config will be sent to the proxy server along with the model.
    When the proxy server builds the generation config, it will:
    1. model_copy(deep=True) this config
    2. Set fields from AnthropicMessagesRequest if they are not already set in the config

    For example: use this config to adjust model temperature, top_p, top_k, max_tokens, etc.

    NOTE: not all fields are usable, e.g. tools must be configured via `tools`,
    system_instruction must be configured via `instruction`.
    """

    output_key: Optional[str] = None
    """Key in session state to store agent output for later use."""

    enable_session: bool = False
    """Whether to enable tRPC-Agent's session for multi-turn conversation.

    If True, the agent will use tRPC-Agent's session to manage conversation history.
        Suitable for service deployments using Session Storage Service(Redis/Mysql).
    If False (default), Claude will maintain its own conversation history for each user.
        Suitable for service deployments using hash-based naming.
    """

    session_config: Optional[SessionConfig] = None
    """Configuration for SessionManager behavior when enable_session is False.

    Controls aspects like session TTL (time-to-live) for idle session cleanup.
    If not provided, default configuration will be used.
    """

    # Internal state
    _resolved_model_key: Optional[str] = None
    """Cached model key after adding LLMModel to proxy."""

    _last_model_name: Optional[str] = None
    """Track last resolved model name for cache invalidation."""

    _runtime: Optional[AsyncRuntime] = None
    """Async runtime for executing async operations in dedicated event loop thread."""

    _session_manager: Optional[SessionManager] = None
    """Session manager for this agent instance."""

    _streaming_tool_names: Optional[Set[str]] = None
    """Set of tool names that support streaming arguments.

    This is populated during _run_async_impl by detecting tools with is_streaming=True.
    Only tools in this set will receive streaming events, matching LlmAgent behavior.
    """

    def __init__(self, **data):
        """Initialize the Claude agent.

        Note: Runtime initialization is deferred to initialize() method.
        """
        super().__init__(**data)

    def initialize(self) -> None:
        """Initialize runtime resources.

        Creates AsyncRuntime and SessionManager if enable_session is False (Claude manages history).
        This method should be called before using the agent.
        """
        # Initialize runtime only when enable_session is False
        # (Claude maintains its own conversation history per session)
        if not self.enable_session:
            if self._runtime is None:
                self._runtime = AsyncRuntime(thread_name="ClaudeAgent")
                self._runtime.start()
                logger.debug("AsyncRuntime event loop thread started")

            if self._session_manager is None:
                # Use session_config if provided, otherwise use defaults
                self._session_manager = SessionManager(
                    runtime=self._runtime,
                    config=self.session_config,
                )
                ttl = self.session_config.ttl if self.session_config else SessionConfig().ttl
                logger.debug("SessionManager initialized for Claude-managed conversation history (ttl=%ss)", ttl)

    def destroy(self) -> None:
        """Destroy runtime resources.

        Closes the session manager, cleans up all connected clients, and shuts down the runtime.
        This method should be called when the agent is no longer needed.
        """
        # Close session manager first
        if self._session_manager is not None:
            try:
                self._session_manager.close()
                logger.debug("SessionManager closed and resources cleaned up")
            except Exception as ex:  # pylint: disable=broad-except
                logger.error("Error closing SessionManager: %s", ex, exc_info=True)
            finally:
                self._session_manager = None

        # Shutdown runtime
        if self._runtime is not None:
            try:
                self._runtime.shutdown()
                logger.debug("AsyncRuntime shutdown completed")
            except Exception as ex:  # pylint: disable=broad-except
                logger.error("Error shutting down AsyncRuntime: %s", ex, exc_info=True)
            finally:
                self._runtime = None

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Core implementation of Claude agent execution.

        Args:
            ctx: Invocation context with session, events, and configuration

        Yields:
            Event: Events from Claude's responses including text, tool calls, and results
        """
        # Ensure runtime is initialized
        if not self.enable_session and self._session_manager is None:
            self.initialize()

        # Detect streaming tools before execution (align with LlmAgent behavior)
        self._streaming_tool_names = await self._detect_streaming_tools(ctx)

        # Ensure model is ready before execution
        model_name = await self._ensure_model_ready(ctx)

        # CHECKPOINT 1: At method entry, after model ready
        await ctx.raise_if_cancelled()

        # Parse agent configuration and build Claude options
        claude_options = await self._parse_agent_config(ctx, model_name)

        # Get message content - check override_messages first, then fall back to session events
        if ctx.override_messages is not None:
            # Mode 1: Use override_messages (controlled by TeamAgent or similar)
            user_prompt = self._convert_override_messages_to_prompt(ctx.override_messages)
            logger.debug("Using override_messages for Claude agent: %s", self.name)
        elif self.enable_session:
            # Mode 2: Build full conversation history from TRPC session
            user_prompt = self._build_prompt_with_history(ctx)
        else:
            # Mode 3: Use only the latest user message (Claude maintains its own conversation context)
            user_prompt = self._extract_latest_user_message(ctx)

        if not user_prompt:
            logger.warning("No user message found in events")
            return

        logger.debug("Sending prompt to Claude: %s...", user_prompt[:100])

        # Determine session ID based on history management strategy
        if self.enable_session:
            # We're managing history ourselves via the trpc-session, so set session id to default.
            # When set to default, it will act as a new session every time query is called.
            claude_session_id = "default"
        else:
            # Let Claude manage history using tRPC-Agent's session ID.
            claude_session_id = ctx.session.id if ctx.session and ctx.session.id else "default"

        logger.debug("Using claude_session_id: %s (enable_session=%s)", claude_session_id, self.enable_session)

        # Create trace reporter for telemetry
        def _text_filter(text: str) -> bool:
            """Filter out placeholder text content."""
            return text and text != "(no content)"

        trace_reporter = CustomTraceReporter(
            agent_name=self.name,
            model_prefix="claude",
            tool_description_prefix="Claude tool",
            text_content_filter=_text_filter,
        )

        # Get or create a persistent client for this Claude session
        try:
            # Only use session manager when enable_session is False
            if not self.enable_session:
                tool_use_map = {}  # {tool_use_id: function_name}
                tool_info_by_index: dict[int, dict] = {}  # {index: {id, name}}

                async for message in self._session_manager.stream_query(
                        session_id=claude_session_id,
                        options=claude_options,
                        prompt=user_prompt,
                ):
                    # CHECKPOINT 2: Each streaming message
                    await ctx.raise_if_cancelled()

                    logger.debug("Received message from Claude: %s", type(message).__name__)

                    event = self._convert_message_to_event(ctx, message, tool_use_map, tool_info_by_index)

                    if event:
                        # Trace event
                        trace_reporter.trace_event(ctx, event)

                        if event.is_final_response():
                            self._save_output_to_state(ctx, event)
                        yield event
            else:
                # For enable_session=True, we need to run in the current event loop
                # Use async context manager for proper lifecycle
                async with ClaudeSDKClient(options=claude_options) as client:
                    logger.debug("Created new client for session '%s' (enable_session=True)", claude_session_id)

                    await client.query(user_prompt, session_id=claude_session_id)

                    tool_use_map = {}  # {tool_use_id: function_name}
                    tool_info_by_index: dict[int, dict] = {}  # {index: {id, name}}

                    async for message in client.receive_response():
                        # CHECKPOINT 2: Each streaming message
                        await ctx.raise_if_cancelled()

                        logger.debug("Received message from Claude: %s", type(message).__name__)

                        event = self._convert_message_to_event(ctx, message, tool_use_map, tool_info_by_index)

                        if event:
                            # Trace event
                            trace_reporter.trace_event(ctx, event)

                            if event.is_final_response():
                                self._save_output_to_state(ctx, event)
                            yield event

        except RunCancelledException:
            # Re-raise to let Runner handle cleanup
            raise

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error during Claude query: %s", ex, exc_info=True)
            # Yield error event
            error_event = Event(
                invocation_id=ctx.invocation_id,
                author=self.name,
                branch=ctx.branch,
                content=Content(
                    role="model",
                    parts=[Part.from_text(text=f"Error: {str(ex)}")],
                ),
                partial=False,
            )
            yield error_event

    async def _ensure_model_ready(self, ctx: InvocationContext) -> str:
        """Ensure model is ready for use, handling factory callbacks and caching.

        Adds the LLMModel to the proxy server and caches the result.

        For static models (non-callback):
        - Cache is reused if already set (no need to re-add)

        For callable models (create_model callback):
        - Always delete old model key (if exists) before adding new model
        - This ensures fresh model registration on each invocation

        Args:
            ctx: Invocation context with run_config.custom_data

        Returns:
            str: Model name/key to use in Claude API calls

        Raises:
            ValueError: If proxy server is not ready or model addition fails
        """
        # Check if model is a callable (create_model callback)
        is_callback = callable(self.model)

        # For static models, return cached key if available
        if not is_callback and self._resolved_model_key:
            logger.debug("Reusing cached model key for static model")
            return self._resolved_model_key

        # Add model to proxy (new model or replacing old one for callbacks)
        try:
            # Resolve model (may invoke model creation callback for callable models)
            model_instance = self.model
            if is_callback:
                custom_data = ctx.run_config.custom_data if ctx.run_config else {}
                model_instance = await self.model(custom_data)

                # For callable models, delete the old model key if it exists
                if self._resolved_model_key:
                    logger.debug("Deleting old model key '%s' before adding new model", self._resolved_model_key)
                    try:
                        _delete_model(self._resolved_model_key)
                    except Exception as ex:  # pylint: disable=broad-except
                        logger.warning("Failed to delete old model '%s': %s", self._resolved_model_key, ex)

            # Add model to proxy and get key, passing generate_content_config
            model_key = _add_model(model_instance, self.generate_content_config)
            self._resolved_model_key = model_key
            self._last_model_name = model_instance.name
            logger.debug("Added LLMModel '%s' to proxy, got key: %s", model_instance.name, model_key)
            return model_key

        except RuntimeError as ex:
            logger.error("Failed to add model to proxy server: %s", ex, exc_info=True)
            raise ValueError(f"Failed to add model to proxy server. "
                             f"Make sure setup_claude_env() was called first. Error: {ex}") from ex

    async def _detect_streaming_tools(self, ctx: InvocationContext) -> Set[str]:
        """Detect which tools support streaming arguments.

        This method checks each tool's is_streaming property to determine which
        tools should receive streaming events. This aligns ClaudeAgent behavior
        with LlmAgent.

        Note: ClaudeAgent converts tools to MCP format with naming convention:
        "mcp__{agent_name}_tools__{tool_name}". This method stores both the
        original tool name and the MCP-prefixed name to ensure matching works
        correctly when Claude returns tool calls.

        Args:
            ctx: The invocation context

        Returns:
            Set of tool names that support streaming (includes both original
            and MCP-prefixed names)
        """
        streaming_names: Set[str] = set()

        if not self.tools:
            return streaming_names

        # MCP server name follows the pattern: {agent_name}_tools
        mcp_server_name = f"{self.name}_tools"

        for tool_item in self.tools:
            if isinstance(tool_item, BaseToolSet):
                toolset_tools = await tool_item.get_tools(ctx)
                for tool in toolset_tools:
                    if getattr(tool, "is_streaming", False):
                        # Add both original name and MCP-prefixed name
                        streaming_names.add(tool.name)
                        streaming_names.add(f"mcp__{mcp_server_name}__{tool.name}")
            elif isinstance(tool_item, BaseTool):
                if getattr(tool_item, "is_streaming", False):
                    # Add both original name and MCP-prefixed name
                    streaming_names.add(tool_item.name)
                    streaming_names.add(f"mcp__{mcp_server_name}__{tool_item.name}")
            elif callable(tool_item):
                func_name = getattr(tool_item, "__name__", str(tool_item))
                if getattr(tool_item, "is_streaming", False):
                    streaming_names.add(func_name)
                    streaming_names.add(f"mcp__{mcp_server_name}__{func_name}")

        if streaming_names:
            logger.debug("Detected %d streaming tool entries: %s", len(streaming_names), streaming_names)

        return streaming_names

    async def _convert_tools_to_mcp(self, ctx: InvocationContext) -> Optional[tuple[dict, List[str]]]:
        """Convert TRPC tools to Claude SDK MCP server configuration.

        Args:
            ctx: The invocation context

        Returns:
            Tuple of (mcp_servers dict, allowed_tools list), or None if no tools
        """
        if not self.tools:
            return None

        # Resolve tools - expand toolsets and convert callables to FunctionTool
        resolved_tools: List[BaseTool] = []

        for tool_item in self.tools:
            if isinstance(tool_item, BaseToolSet):
                # Expand toolset to individual tools
                toolset_tools = await tool_item.get_tools(ctx)
                resolved_tools.extend(toolset_tools)
            elif isinstance(tool_item, BaseTool):
                resolved_tools.append(tool_item)
            elif callable(tool_item):
                # Wrap callable in FunctionTool
                function_tool = FunctionTool(tool_item)
                resolved_tools.append(function_tool)
            else:
                logger.warning("Unsupported tool type: %s", type(tool_item))

        if not resolved_tools:
            return None

        # Convert TRPC tools to Claude SDK tools
        sdk_tools: List[SdkMcpTool] = []
        tool_names: List[str] = []

        for trpc_tool in resolved_tools:
            try:
                sdk_tool = self._convert_tool_to_sdk_tool(trpc_tool, ctx)
                sdk_tools.append(sdk_tool)
                tool_names.append(trpc_tool.name)
                logger.debug("Converted tool '%s' to Claude SDK MCP tool", trpc_tool.name)
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Failed to convert tool '%s': %s", trpc_tool.name, ex, exc_info=True)

        if not sdk_tools:
            return None

        # Create MCP server with the tools
        server_name = f"{self.name}_tools"
        mcp_server = create_sdk_mcp_server(
            name=server_name,
            version="1.0.0",
            tools=sdk_tools,
        )

        logger.debug("Created MCP server with %s tools for agent '%s'", len(sdk_tools), self.name)

        # Build allowed_tools list with mcp__servername__toolname format
        allowed_tools = [f"mcp__{server_name}__{tool_name}" for tool_name in tool_names]

        return ({server_name: mcp_server}, allowed_tools)

    def _convert_tool_to_sdk_tool(self, trpc_tool: BaseTool, ctx: InvocationContext) -> SdkMcpTool:
        """Convert a single TRPC BaseTool to Claude SDK SdkMcpTool.

        Args:
            trpc_tool: The TRPC tool to convert
            ctx: The invocation context

        Returns:
            SdkMcpTool ready for use with Claude SDK

        Raises:
            ValueError: If tool cannot be converted
        """
        # Get the function declaration to extract parameter schema
        func_decl = trpc_tool._get_declaration()
        if not func_decl:
            logger.error("Tool '%s' has no function declaration", trpc_tool.name)
            raise ValueError(f"Tool '{trpc_tool.name}' has no function declaration")

        # Convert TRPC schema to Claude SDK input_schema
        input_schema = self._convert_schema_to_dict(func_decl.parameters) if func_decl.parameters else {}

        # Capture the current event loop (the _run_async_impl loop)
        target_loop = asyncio.get_event_loop()

        # Create async handler that wraps the TRPC tool's run_async
        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            """Handler that executes the TRPC tool and formats the response."""
            try:
                # Submit the tool execution to the target loop (_run_async_impl loop)
                future = asyncio.run_coroutine_threadsafe(trpc_tool.run_async(tool_context=ctx, args=args), target_loop)
                result = await asyncio.wrap_future(future)

                # Format result for Claude SDK
                if isinstance(result, dict):
                    # If result already has content format, use it
                    if "content" in result:
                        return result
                    # Otherwise wrap it
                    result_text = str(result)
                elif result is None:
                    result_text = "Success"
                else:
                    result_text = str(result)

                return {"content": [{"type": "text", "text": result_text}]}

            except Exception as ex:  # pylint: disable=broad-except
                logger.error("Error executing tool '%s': %s", trpc_tool.name, ex, exc_info=True)
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Error: {str(ex)}"
                    }],
                    "is_error": True,
                }

        # Create and return SdkMcpTool
        return SdkMcpTool(
            name=trpc_tool.name,
            description=trpc_tool.description or "",
            input_schema=input_schema,
            handler=handler,
        )

    def _convert_schema_to_dict(self, schema) -> dict[str, Any]:
        """Convert TRPC Schema to dict format for Claude SDK.

        Args:
            schema: TRPC Schema object

        Returns:
            Dictionary mapping parameter names to types
        """
        if not schema or not schema.properties:
            return {}

        # Build a simple dict mapping param names to Python types
        input_schema = {}

        for param_name, param_schema in schema.properties.items():
            # Extract type from schema
            param_type = self._get_python_type_from_schema(param_schema)
            input_schema[param_name] = param_type

        return input_schema

    def _get_python_type_from_schema(self, param_schema) -> type:
        """Get Python type from TRPC Schema parameter.

        Args:
            param_schema: Parameter schema object

        Returns:
            Python type (str, int, float, bool, etc.)
        """
        if not param_schema or not param_schema.type:
            return str  # Default to string

        schema_type = param_schema.type

        # Map TRPC Schema types to Python types
        type_mapping = {
            Type.STRING: str,
            Type.INTEGER: int,
            Type.NUMBER: float,
            Type.BOOLEAN: bool,
            Type.OBJECT: dict,
            Type.ARRAY: list,
        }

        return type_mapping.get(schema_type, str)

    def _get_entry_point_dir(self) -> Optional[str]:
        """Get the directory of the entry point (main script).

        Returns:
            Path to the entry point directory, or None if not found
        """
        try:
            # Try to get the main module
            main_module = sys.modules.get("__main__")
            if main_module and hasattr(main_module, "__file__"):
                main_file = main_module.__file__
                if main_file:
                    entry_dir = str(Path(main_file).parent.resolve())
                    return entry_dir
            logger.warning("Failed to get entry point directory")
            return None

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Failed to get entry point directory: %s", ex, exc_info=True)
            return None

    async def _parse_agent_config(self, ctx: InvocationContext, model_name: str) -> ClaudeAgentOptions:
        """Parse agent configuration and return ClaudeAgentOptions.

        This method merges the claude_agent_options constructor parameter with the agent's
        model, instruction, and tools properties.

        Args:
            ctx: The invocation context
            model_name: The model name/key to use (from _ensure_model_ready)

        Returns:
            ClaudeAgentOptions configured for this execution
        """
        # Start with options from constructor if provided, otherwise create new one
        if self.claude_agent_options:
            options = self.claude_agent_options
        else:
            options = ClaudeAgentOptions()

        # Set cwd to entry point directory if not already set
        if not options.cwd:
            entry_point_dir = self._get_entry_point_dir()
            if entry_point_dir:
                options.cwd = entry_point_dir
                logger.debug("Set cwd to entry point directory: %s", entry_point_dir)

        # Set the model name
        options.model = model_name

        # Set streaming by using run_config
        options.include_partial_messages = ctx.run_config.streaming
        logger.debug("Set include_partial_messages=%s based on streaming config", options.include_partial_messages)

        # Process instruction with template substitution
        if self.instruction:
            if callable(self.instruction):
                # InstructionProvider callable
                if inspect.iscoroutinefunction(self.instruction):
                    system_prompt = await self.instruction(ctx)
                else:
                    system_prompt = self.instruction(ctx)
            else:
                # String instruction with template substitution
                system_prompt = self._apply_template_substitution(self.instruction, ctx)

            options.system_prompt = system_prompt

        # Convert and add tools as MCP server if configured
        if self.tools:
            tools_result = await self._convert_tools_to_mcp(ctx)
            if tools_result:
                mcp_servers, allowed_tools = tools_result

                # Merge with any existing mcp_servers from config
                if isinstance(options.mcp_servers, dict):
                    mcp_servers.update(options.mcp_servers)
                options.mcp_servers = mcp_servers

                # Merge with any existing allowed_tools
                if options.allowed_tools:
                    allowed_tools = list(set(allowed_tools + options.allowed_tools))  # Deduplicate

                if allowed_tools:
                    options.allowed_tools = allowed_tools
                    logger.debug("Pre-approved %s tools: %s", len(allowed_tools), allowed_tools)

        logger.debug("Claude options: %s", options)

        return options

    def _save_output_to_state(self, ctx: InvocationContext, event: Event) -> None:
        """Save agent output to session state if output_key is configured.

        Args:
            ctx: The invocation context
            event: The event containing the content to save
        """
        if self.output_key and event.content and event.content.parts:
            # Save output to state using the delta tracking system
            result = "".join([part.text for part in event.content.parts if part.text])
            if result:  # Only save non-empty results
                ctx.state[self.output_key] = result
                event.actions.state_delta[self.output_key] = result
                logger.debug("Saved agent output to state key '%s': %s...", self.output_key, result[:100])

    def _apply_template_substitution(self, instruction: str, ctx: InvocationContext) -> str:
        """Apply template substitution to replace {key} placeholders with state values.

        Supports:
        - {var} - Required variable, left as-is if not found
        - {var?} - Optional variable, replaced with empty string if not found

        Args:
            instruction: Instruction string with template placeholders
            ctx: Invocation context with session state

        Returns:
            Instruction with placeholders replaced
        """
        if not instruction or "{" not in instruction:
            return instruction

        # Get state from session
        state_dict = ctx.session.state if ctx.session else {}

        try:

            def replace_placeholder(match):
                """Replace a single placeholder with its value."""
                var_name = match.group().lstrip("{").rstrip("}").strip()
                optional = False

                # Handle optional variables (ending with ?)
                if var_name.endswith("?"):
                    optional = True
                    var_name = var_name.removesuffix("?")

                # Check if variable exists in state
                if var_name in state_dict:
                    value = state_dict[var_name]
                    return str(value) if value is not None else ""
                else:
                    if optional:
                        return ""
                    else:
                        # Leave placeholder unchanged for required vars
                        return match.group()

            # Match {variable_name} patterns
            pattern = r"\{[^{}]*\}"
            result = re.sub(pattern, replace_placeholder, instruction)

            if result != instruction:
                logger.debug("Template substitution applied: %s... -> %s...", instruction[:50], result[:50])

            return result

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Template substitution failed: %s", ex, exc_info=True)
            return instruction

    def _extract_latest_user_message(self, ctx: InvocationContext) -> Optional[str]:
        """Extract the latest user message from session events.

        Args:
            ctx: Invocation context with session events

        Returns:
            Latest user message text, or None if not found
        """
        if not ctx.session or not ctx.session.events:
            return None

        # Look through events in reverse to find latest user message
        for event in reversed(ctx.session.events):
            if not event.is_model_visible():
                continue
            if event.author == "user" and event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        return part.text

        return None

    def _convert_override_messages_to_prompt(self, override_messages: list) -> Optional[str]:
        """Convert override_messages (Content objects) to a prompt string for Claude.

        This method converts the Content objects from TeamAgent into a formatted
        prompt string suitable for Claude SDK.

        Args:
            override_messages: List of Content objects from TeamAgent

        Returns:
            Formatted prompt string, or None if no valid content found
        """
        prompt_parts = []

        for content in override_messages:
            if not isinstance(content, Content) or not content.parts:
                continue

            role = content.role or "user"

            for part in content.parts:
                if part.text:
                    if role == "user":
                        prompt_parts.append(f"User: {part.text}")
                    else:
                        prompt_parts.append(f"Assistant: {part.text}")
                elif part.function_call:
                    # Include function call information as context
                    func_call = part.function_call
                    args_str = str(func_call.args) if func_call.args else "{}"
                    prompt_parts.append(f"[Called function '{func_call.name}' with args: {args_str}]")
                elif part.function_response:
                    # Include function response information as context
                    func_resp = part.function_response
                    response_str = str(func_resp.response) if func_resp.response else "Success"
                    prompt_parts.append(f"[Function '{func_resp.name}' returned: {response_str}]")

        if not prompt_parts:
            return None

        return "\n".join(prompt_parts)

    def _build_prompt_with_history(self, ctx: InvocationContext) -> Optional[str]:
        """Build a prompt with full conversation history from TRPC session.

        This method extracts the conversation history from session events and formats
        it as a readable conversation string, suitable for multi-instance deployments
        where Claude's built-in history is not available.

        Args:
            ctx: Invocation context with session events

        Returns:
            Formatted prompt with conversation history, or None if no messages found
        """
        if not ctx.session or not ctx.session.events:
            return None

        conversation_parts = []
        latest_user_message = None

        # Iterate through events to build conversation history
        for event in ctx.session.events:
            if not event.is_model_visible():
                continue
            if not event.content or not event.content.parts:
                continue

            if event.author == "user":
                # Extract user message
                for part in event.content.parts:
                    if part.text:
                        # Store the latest user message separately
                        latest_user_message = part.text
                        # Also add to history (except the very last one, we'll add it separately)
                        conversation_parts.append(f"User: {part.text}")
                        break

            elif event.author == self.name:
                # Extract agent response
                agent_message = self._format_agent_message_parts(event.content.parts)
                if agent_message:
                    conversation_parts.append(f"Assistant: {agent_message}")

        if not latest_user_message:
            return None

        # If there's conversation history (more than just the latest message)
        if len(conversation_parts) > 1:
            # Remove the latest user message from history (we'll add it separately)
            history_parts = conversation_parts[:-1]

            # Build the final prompt with history
            prompt = "Previous conversation:\n"
            prompt += "\n".join(history_parts)
            prompt += f"\n\nCurrent message:\nUser: {latest_user_message}"

            logger.debug("Built prompt with %s history messages", len(history_parts))
            return prompt
        else:
            # No history, just return the latest message
            return latest_user_message

    def _format_agent_message_parts(self, parts: List[Part]) -> str:
        """Format agent message parts into a readable string.

        Args:
            parts: List of Part objects from an agent message

        Returns:
            Formatted string representation of the message
        """
        formatted_parts = []

        for part in parts:
            if part.text:
                # Regular text response
                formatted_parts.append(part.text)

            elif part.thought:
                # Thinking/reasoning
                formatted_parts.append(f"[Thinking: {part.thought}]")

            elif part.function_call:
                # Function call
                func_call = part.function_call
                args_str = str(func_call.args) if func_call.args else "{}"
                formatted_parts.append(f"[Called function '{func_call.name}' with args: {args_str}]")

            elif part.function_response:
                # Function response
                func_resp = part.function_response
                response_str = str(func_resp.response) if func_resp.response else "Success"
                formatted_parts.append(f"[Function '{func_resp.name}' returned: {response_str}]")

        return " ".join(formatted_parts) if formatted_parts else ""

    def _convert_message_to_event(
        self,
        ctx: InvocationContext,
        message,
        tool_use_map: dict[str, str],
        tool_info_by_index: Optional[dict[int, dict]] = None,
    ) -> Optional[Event]:
        """Convert Claude SDK message to TRPC Event.

        Args:
            ctx: Invocation context
            message: Claude SDK message (AssistantMessage, SystemMessage, ResultMessage, StreamEvent, UserMessage, etc.)
            tool_use_map: Mapping from tool_use_id to function name
            tool_info_by_index: Optional dict mapping tool index to tool info for streaming tool calls.
                               Each entry contains: {"id": str, "name": str}

        Returns:
            Event or None if message type is not supported
        """
        if isinstance(message, AssistantMessage):
            return self._convert_assistant_message(ctx, message, tool_use_map)

        elif isinstance(message, UserMessage):
            return self._convert_user_message(ctx, message, tool_use_map)

        elif isinstance(message, StreamEvent):
            return self._convert_streaming_event(ctx, message, tool_info_by_index)

        elif isinstance(message, SystemMessage):
            # System messages are informational, can log or skip
            logger.debug("System message: %s - %s", message.subtype, message.data)
            return None

        elif isinstance(message, ResultMessage):
            # Result message contains cost and usage info
            logger.debug("Claude query complete: turns=%s, duration=%sms, cost=$%s (if available)", message.num_turns,
                         message.duration_ms, message.total_cost_usd)
            return None

        else:
            logger.debug("Unhandled message type: %s", type(message))
            return None

    def _convert_assistant_message(self, ctx: InvocationContext, message: AssistantMessage,
                                   tool_use_map: dict[str, str]) -> Optional[Event]:
        """Convert AssistantMessage to TRPC Event.

        Args:
            ctx: Invocation context
            message: AssistantMessage from Claude SDK
            tool_use_map: Mapping from tool_use_id to function name (will be updated)

        Returns:
            Event or None
        """
        parts = []

        for block in message.content:
            if isinstance(block, TextBlock):
                parts.append(Part.from_text(text=block.text))

            elif isinstance(block, ThinkingBlock):
                # Include thinking as thought part
                parts.append(Part.from_thought(thought=block.thinking))

            elif isinstance(block, ToolUseBlock):
                # Convert to function call
                func_call = Part.from_function_call(name=block.name, args=block.input)
                func_call.function_call.id = block.id
                parts.append(func_call)

                # Track tool_use_id to function name mapping
                tool_use_map[block.id] = block.name

            elif isinstance(block, ToolResultBlock):
                # Convert to function response
                # Parse content to dict if string
                if isinstance(block.content, str):
                    response = {"result": block.content}
                elif isinstance(block.content, list):
                    # Extract text from list of content blocks
                    text_parts = []
                    for item in block.content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text_parts.append(item.get("text", ""))
                    response = {"result": "\n".join(text_parts)}
                else:
                    response = block.content or {}

                func_resp = Part.from_function_response(
                    name="tool_result",  # Placeholder name
                    response=response,
                )
                func_resp.function_response.id = block.tool_use_id
                parts.append(func_resp)

        if parts:
            event = Event(
                invocation_id=ctx.invocation_id,
                author=self.name,
                branch=ctx.branch,
                content=Content(role="model", parts=parts),
                partial=False,
            )
            return event

        return None

    def _convert_user_message(self, ctx: InvocationContext, message: UserMessage,
                              tool_use_map: dict[str, str]) -> Optional[Event]:
        """Convert UserMessage to TRPC Event.

        UserMessage contains tool results being sent back to Claude.

        Args:
            ctx: Invocation context
            message: UserMessage from Claude SDK
            tool_use_map: Mapping from tool_use_id to function name

        Returns:
            Event or None
        """
        parts = []

        if isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    # Parse content to dict if string
                    if isinstance(block.content, str):
                        response = {"result": block.content}
                    elif isinstance(block.content, list):
                        # Extract text from list of content blocks
                        text_parts = []
                        for item in block.content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                text_parts.append(item.get("text", ""))
                        response = {"result": "\n".join(text_parts)}
                    else:
                        response = block.content or {}

                    # Get function name from tool_use_map
                    function_name = tool_use_map.get(block.tool_use_id, "tool_result")

                    func_resp = Part.from_function_response(
                        name=function_name,
                        response=response,
                    )
                    func_resp.function_response.id = block.tool_use_id

                    parts.append(func_resp)
                    logger.debug("Parsed tool result for tool_use_id=%s, function=%s", block.tool_use_id, function_name)

        if parts:
            event = Event(
                invocation_id=ctx.invocation_id,
                author=self.name,
                branch=ctx.branch,
                content=Content(role="model", parts=parts),
                partial=False,
            )
            return event

        return None

    def _convert_streaming_event(
        self,
        ctx: InvocationContext,
        stream_event: StreamEvent,
        tool_info_by_index: Optional[dict[int, dict]] = None,
    ) -> Optional[Event]:
        """Convert StreamEvent to TRPC Event.

        Args:
            ctx: Invocation context
            stream_event: StreamEvent from Claude SDK
            tool_info_by_index: Optional dict mapping tool index to tool info (id, name).
                               Will be updated in-place for tool_use blocks.

        Returns:
            Event or None. Emits events for text_delta (partial text chunks) and
            input_json_delta (streaming tool call arguments).
            The final complete message will come in an AssistantMessage.
        """
        event_data = stream_event.event
        event_type = event_data.get("type")

        # Handle content_block_start to track new tool_use blocks
        if event_type == "content_block_start":
            content_block = event_data.get("content_block", {})
            if content_block.get("type") == "tool_use" and tool_info_by_index is not None:
                index = event_data.get("index", 0)
                tool_info_by_index[index] = {
                    "id": content_block.get("id", ""),
                    "name": content_block.get("name", ""),
                }
                logger.debug(f"Stream: tool_use block started at index {index}, name={content_block.get('name')}")
            return None

        # Handle content_block_delta for text and tool input streaming
        if event_type == "content_block_delta":
            delta = event_data.get("delta", {})
            delta_type = delta.get("type")

            if delta_type == "text_delta":
                # Text content delta - emit as partial event
                text_chunk = delta.get("text", "")

                if text_chunk:  # Only emit if there's actual text
                    event = Event(
                        invocation_id=ctx.invocation_id,
                        author=self.name,
                        branch=ctx.branch,
                        content=Content(role="model", parts=[Part.from_text(text=text_chunk)]),
                        partial=True,  # Mark as partial for streaming
                    )
                    return event

            elif delta_type == "input_json_delta":
                # Tool input JSON delta - emit as streaming tool call event
                index = event_data.get("index", 0)
                partial_json = delta.get("partial_json", "")

                if tool_info_by_index is not None and index in tool_info_by_index and partial_json:
                    tool_info = tool_info_by_index[index]
                    tool_name = tool_info["name"]

                    # Only emit streaming events for tools that have is_streaming=True
                    # This aligns ClaudeAgent behavior with LlmAgent
                    if self._streaming_tool_names and tool_name not in self._streaming_tool_names:
                        # Skip streaming events for non-streaming tools
                        return None

                    # Create function call part with delta only
                    # Consumers handle streaming events through Runner.run_async()
                    function_part = Part.from_function_call(name=tool_name, args={TOOL_STREAMING_ARGS: partial_json})
                    if tool_info["id"]:
                        function_part.function_call.id = tool_info["id"]

                    event = Event(
                        invocation_id=ctx.invocation_id,
                        author=self.name,
                        branch=ctx.branch,
                        content=Content(role="model", parts=[function_part]),
                        partial=True,
                        custom_metadata={
                            "streaming_tool_call": True,
                            "tool_call_args_complete": False,
                        },
                    )
                    return event

            return None

        # Log other event types for debugging but don't emit events
        if event_type in (
                "message_start",
                "content_block_stop",
                "message_delta",
                "message_stop",
        ):
            logger.debug("Stream: %s", event_type)
        else:
            logger.debug("Stream: unhandled event type %s", event_type)

        return None
