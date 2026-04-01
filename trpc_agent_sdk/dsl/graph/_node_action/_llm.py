# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""LLM node action executor.

This module provides the LLMNodeAction class for executing LLM calls
within graph nodes, implementing three-stage message selection.
"""

import asyncio
import inspect
import json
import uuid
from typing import Any
from typing import Optional

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Part

from .._constants import ROLE_MODEL
from .._constants import ROLE_USER
from .._constants import STATE_KEY_LAST_RESPONSE
from .._constants import STATE_KEY_LAST_RESPONSE_ID
from .._constants import STATE_KEY_LAST_TOOL_RESPONSE
from .._constants import STATE_KEY_MESSAGES
from .._constants import STATE_KEY_NODE_RESPONSES
from .._constants import STATE_KEY_ONE_SHOT_MESSAGES
from .._constants import STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE
from .._constants import STATE_KEY_USER_INPUT
from .._event_writer import AsyncEventWriter
from .._event_writer import EventWriter
from .._state import State
from ._base import BaseNodeAction


class LLMNodeAction(BaseNodeAction):
    """Executes LLM node with three-stage message selection.

    Implements the three-stage rule from trpc-agent-go:
    1. One-shot stage: Use STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE[name] or STATE_KEY_ONE_SHOT_MESSAGES (consumed)
    2. User-input stage: Use history + current STATE_KEY_USER_INPUT
    3. History stage: Just use conversation history

    Attributes:
        model: LLM model instance
        instruction: System instruction
        tools: Available tools
        generation_config: Optional generation configuration
    """

    def __init__(
        self,
        name: str,
        model: LLMModel,
        instruction: str,
        tools: dict[str, Any],
        *,
        tool_parallel: bool = False,
        max_tool_iterations: int = 8,
        generation_config: Optional[GenerateContentConfig],
        writer: EventWriter,
        async_writer: AsyncEventWriter,
        ctx: Optional[InvocationContext] = None,
    ):
        """Initialize the LLM node action.

        Args:
            name: Node name
            model: LLM model instance
            instruction: System instruction
            tools: Available tools
            tool_parallel: Whether to execute tool calls in parallel within one round
            max_tool_iterations: Maximum model->tool loop rounds
            generation_config: Optional generation configuration
            writer: EventWriter for high-frequency streaming text
            async_writer: AsyncEventWriter for lifecycle events
            ctx: Optional invocation context
        """
        super().__init__(name, writer, async_writer, ctx)
        self.model = model
        self.instruction = instruction
        self.tools = tools
        self.tool_parallel = tool_parallel
        self.max_tool_iterations = max_tool_iterations
        self.generation_config = generation_config

    def _convert_foreign_tool_messages(self, messages: list[Content]) -> list[Content]:
        """Convert function_call/function_response to text for tools not in self.tools.

        When message history contains tool interactions from previous nodes that used
        different tools, we need to convert those to plain text to avoid API errors.
        The API requires tool definitions when the message history contains tool_calls.

        This method:
        1. Collects all function_call IDs and their matching function_response
        2. For each function_call not in self.tools, converts it and its response to text
        3. Preserves the conversation context while avoiding format errors

        Args:
            messages: List of Content messages that may contain function_call/function_response

        Returns:
            New list of Content messages with foreign tool interactions converted to text
        """
        if not messages:
            return messages

        # Get set of tool names available to this node
        available_tool_names = set(self.tools.keys()) if self.tools else set()

        # First pass: collect function_response by ID for matching
        response_by_id: dict[str, tuple[int, int, FunctionResponse]] = {}
        for msg_idx, msg in enumerate(messages):
            if not msg.parts:
                continue
            for part_idx, part in enumerate(msg.parts):
                if part.function_response and part.function_response.id:
                    response_by_id[part.function_response.id] = (msg_idx, part_idx, part.function_response)

        # Second pass: identify which function_calls need conversion
        # Track (msg_idx, part_idx) pairs that need conversion
        calls_to_convert: set[tuple[int, int]] = set()  # (msg_idx, part_idx)
        responses_to_convert: set[tuple[int, int]] = set()  # (msg_idx, part_idx)

        for msg_idx, msg in enumerate(messages):
            if not msg.parts:
                continue
            for part_idx, part in enumerate(msg.parts):
                if part.function_call:
                    tool_name = part.function_call.name
                    # Check if this tool is NOT in our available tools
                    if tool_name not in available_tool_names:
                        calls_to_convert.add((msg_idx, part_idx))
                        # Find matching response by ID
                        call_id = part.function_call.id
                        if call_id and call_id in response_by_id:
                            resp_msg_idx, resp_part_idx, _ = response_by_id[call_id]
                            responses_to_convert.add((resp_msg_idx, resp_part_idx))

        # If nothing to convert, return original messages
        if not calls_to_convert and not responses_to_convert:
            return messages

        # Third pass: build new messages list with conversions
        result: list[Content] = []
        for msg_idx, msg in enumerate(messages):
            if not msg.parts:
                result.append(msg)
                continue

            new_parts: list[Part] = []
            for part_idx, part in enumerate(msg.parts):
                if part.function_call and (msg_idx, part_idx) in calls_to_convert:
                    # Convert function_call to text
                    fc = part.function_call
                    args_str = json.dumps(fc.args, ensure_ascii=False) if fc.args else "{}"
                    text = f"[Tool Call: {fc.name}({args_str})]"
                    new_parts.append(Part.from_text(text=text))
                elif part.function_response and (msg_idx, part_idx) in responses_to_convert:
                    # Convert function_response to text
                    fr = part.function_response
                    resp_str = json.dumps(fr.response, ensure_ascii=False) if fr.response else "{}"
                    text = f"[Tool Response ({fr.name}): {resp_str}]"
                    new_parts.append(Part.from_text(text=text))
                else:
                    # Keep original part
                    new_parts.append(part)

            # Create new Content with converted parts
            result.append(Content(role=msg.role, parts=new_parts))

        return result

    def _build_generation_config(self) -> GenerateContentConfig:
        """Build a fresh generation config to avoid cross-invocation mutation."""
        if self.generation_config is not None:
            config = self.generation_config.model_copy(deep=True)
        else:
            config = GenerateContentConfig()
        config.system_instruction = self.instruction
        return config

    @staticmethod
    def _extract_input_text(messages: list[Content]) -> str:
        if not messages:
            return ""
        last_content = messages[-1]
        if not last_content.parts:
            return ""
        for part in last_content.parts:
            if part.text:
                return part.text
        return ""

    @staticmethod
    def _collect_tool_calls(parts: list[Part]) -> list[FunctionCall]:
        return [part.function_call for part in parts if part.function_call is not None]

    @staticmethod
    def _build_response_content(response_parts: list[Part], response_text: str) -> Optional[Content]:
        if response_parts:
            return Content(role=ROLE_MODEL, parts=response_parts)
        if response_text:
            return Content(role=ROLE_MODEL, parts=[Part.from_text(text=response_text)])
        return None

    async def _run_model_round(
        self,
        *,
        messages: list[Content],
        ctx: Optional[InvocationContext],
    ) -> tuple[str, str, list[Part]]:
        request = LlmRequest(
            model=self.model.name,
            contents=messages,
            config=self._build_generation_config(),
        )

        if self.tools:
            request.append_tools(list(self.tools.values()))

        input_text = self._extract_input_text(messages)
        await self.async_writer.write_model_start(self.model.name, input_text)

        try:
            response_text = ""
            response_id = ""
            response_parts: list[Part] = []

            logger.debug(f"[{self.name}] Calling LLM model: {self.model.name}")
            async for llm_response in self.model.generate_async(request, stream=True, ctx=ctx):
                if llm_response.response_id:
                    response_id = llm_response.response_id

                if not llm_response.content or not llm_response.content.parts:
                    continue

                if llm_response.partial:
                    for part in llm_response.content.parts:
                        if part.text:
                            self.writer.write_text(part.text, partial=True)
                    continue

                response_parts = list(llm_response.content.parts)
                text_parts = [part.text for part in response_parts if part.text]
                response_text = "".join(text_parts)

            logger.debug(f"[{self.name}] LLM response received ({len(response_text)} chars)")
            await self.async_writer.write_model_complete(
                self.model.name,
                input_text=input_text,
                output_text=response_text,
            )
            return response_text, response_id, response_parts
        except Exception as e:
            logger.error(f"[{self.name}] LLM node failed: {e}", exc_info=True)
            await self.async_writer.write_model_complete(self.model.name, input_text=input_text, error=str(e))
            raise RuntimeError(f"LLM node '{self.name}' failed: {e}") from e

    async def _invoke_tool(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        ctx: InvocationContext,
    ) -> Any:
        run_async = getattr(tool, "run_async", None)
        if callable(run_async):
            try:
                signature = inspect.signature(run_async)
            except (TypeError, ValueError):
                signature = None

            if signature and "tool_context" in signature.parameters:
                return await run_async(tool_context=ctx, args=tool_args)
            return await run_async(tool_args)

        run = getattr(tool, "run", None)
        if callable(run):
            return run(tool_args)

        if asyncio.iscoroutinefunction(tool):
            return await tool(tool_args)

        if callable(tool):
            return tool(tool_args)

        raise TypeError(f"Tool '{type(tool).__name__}' is not callable")

    async def _execute_tool_round(
        self,
        *,
        function_calls: list[FunctionCall],
        ctx: InvocationContext,
    ) -> tuple[list[Content], str]:
        normalized_calls: list[dict[str, Any]] = []
        for function_call in function_calls:
            tool_name = function_call.name if isinstance(function_call.name, str) else ""
            raw_tool_args = function_call.args
            tool_args = raw_tool_args if isinstance(raw_tool_args, dict) else {}
            tool_call_id = function_call.id if isinstance(function_call.id, str) and function_call.id else ""
            if not tool_call_id:
                tool_call_id = f"{tool_name}_{uuid.uuid4().hex[:8]}"
            normalized_calls.append({
                "tool_name": tool_name,
                "tool_args": tool_args,
                "tool_call_id": tool_call_id,
            })

        # Emit visible function_call events so runner/UI can observe the tool plan.
        for normalized_call in normalized_calls:
            function_call_event_content = Content(
                role=ROLE_MODEL,
                parts=[
                    Part(function_call=FunctionCall(
                        name=normalized_call["tool_name"],
                        id=normalized_call["tool_call_id"],
                        args=normalized_call["tool_args"],
                    ))
                ],
            )
            self.writer.write_content(function_call_event_content, partial=False)

        async def execute_single_tool(normalized_call: dict[str, Any]) -> dict[str, Any]:
            tool_name = normalized_call["tool_name"]
            tool_args = normalized_call["tool_args"]
            tool_call_id = normalized_call["tool_call_id"]
            input_args = json.dumps(tool_args, ensure_ascii=False) if tool_args else ""
            await self.async_writer.write_tool_start(tool_name, tool_call_id, input_args)

            if tool_name not in self.tools:
                error = f"Tool '{tool_name}' not found"
                await self.async_writer.write_tool_complete(tool_name, tool_call_id, input_args=input_args, error=error)
                return {"name": tool_name, "id": tool_call_id, "error": error}

            tool = self.tools[tool_name]
            try:
                result = await self._invoke_tool(tool=tool, tool_args=tool_args, ctx=ctx)
                output_result = result if isinstance(result, str) else json.dumps(
                    result, ensure_ascii=False, default=str)
                await self.async_writer.write_tool_complete(
                    tool_name,
                    tool_call_id,
                    input_args=input_args,
                    output_result=output_result,
                )
                return {"name": tool_name, "id": tool_call_id, "result": result}
            except Exception as e:
                error = str(e)
                await self.async_writer.write_tool_complete(tool_name, tool_call_id, input_args=input_args, error=error)
                return {"name": tool_name, "id": tool_call_id, "error": error}

        if self.tool_parallel and len(normalized_calls) > 1:
            tool_results = await asyncio.gather(*[execute_single_tool(call_info) for call_info in normalized_calls])
        else:
            tool_results = []
            for normalized_call in normalized_calls:
                tool_results.append(await execute_single_tool(normalized_call))

        tool_messages: list[Content] = []
        last_tool_response = ""
        for tool_result in tool_results:
            if "error" in tool_result:
                response_data = {"error": tool_result["error"]}
            else:
                response_data = tool_result["result"]
                if isinstance(response_data, str):
                    last_tool_response = response_data
                else:
                    last_tool_response = json.dumps(response_data, ensure_ascii=False, default=str)

            function_response = FunctionResponse(
                name=tool_result["name"],
                id=tool_result["id"],
                response=response_data,
            )
            function_response_content = Content(role=ROLE_USER, parts=[Part(function_response=function_response)])
            # Emit visible function_response events to mirror classic tool flow.
            self.writer.write_content(function_response_content, partial=False)
            tool_messages.append(function_response_content)

        return tool_messages, last_tool_response

    async def execute(self, state: State) -> dict[str, Any]:
        """Execute the LLM node with three-stage message selection.

        Args:
            state: Current state

        Returns:
            State update dictionary
        """
        # Determine which stage to execute
        one_shot_by_node = state.get(STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE, {})
        one_shot_global = state.get(STATE_KEY_ONE_SHOT_MESSAGES, [])
        user_input = state.get(STATE_KEY_USER_INPUT, "")
        history = list(state.get(STATE_KEY_MESSAGES, []))

        # Track state updates for clearing consumed one-shot messages
        clear_update: dict[str, Any] = {}
        messages_to_use: list[Content] = []
        user_content_to_add: Optional[Content] = None  # Track if we need to persist user input to history

        # Stage 1: Check STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE
        if self.name in one_shot_by_node and one_shot_by_node[self.name]:
            messages_to_use = list(one_shot_by_node[self.name])
            # Clear this node's one-shot messages from STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE
            updated_by_node = dict(one_shot_by_node)
            del updated_by_node[self.name]
            clear_update[STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE] = updated_by_node
        # Stage 1b: Check STATE_KEY_ONE_SHOT_MESSAGES
        elif one_shot_global:
            messages_to_use = list(one_shot_global)
            # Clear global one-shot messages in STATE_KEY_ONE_SHOT_MESSAGES
            clear_update[STATE_KEY_ONE_SHOT_MESSAGES] = []
        # Stage 2: STATE_KEY_USER_INPUT stage
        elif user_input:
            messages_to_use = list(history)
            # Only add STATE_KEY_USER_INPUT if it's not already the last message in history
            # This prevents duplication when history already contains the user message
            # Check both with role=user and without role (from session events)
            should_add_user_input = True
            if messages_to_use:
                last_msg = messages_to_use[-1]
                # Check if last message is a user message (role is 'user' or None/unset)
                is_user_msg = last_msg.role in (ROLE_USER, None, "")
                if is_user_msg and last_msg.parts:
                    last_text = next((p.text for p in last_msg.parts if p.text), "")
                    if last_text == user_input:
                        should_add_user_input = False

            if should_add_user_input:
                user_content_to_add = Content(role=ROLE_USER, parts=[Part.from_text(text=user_input)])
                messages_to_use.append(user_content_to_add)
            # Clear STATE_KEY_USER_INPUT after use
            clear_update[STATE_KEY_USER_INPUT] = ""
        # Stage 3: History stage
        else:
            messages_to_use = list(history)

        # Convert function_call/function_response to text for tools not in self.tools
        # This preserves conversation context while avoiding API errors when
        # message history contains tool interactions from other nodes
        conversation_messages = self._convert_foreign_tool_messages(messages_to_use)
        messages_update: list[Content] = []
        if user_content_to_add is not None:
            messages_update.append(user_content_to_add)

        final_response_text = ""
        final_response_id = ""
        last_tool_response = ""

        ctx = self.ctx
        tool_iterations = 0
        while True:
            response_text, response_id, response_parts = await self._run_model_round(
                messages=conversation_messages,
                ctx=ctx,
            )

            response_content = self._build_response_content(response_parts, response_text)
            if response_content is not None:
                conversation_messages.append(response_content)
                messages_update.append(response_content)

            final_response_text = response_text
            if response_id:
                final_response_id = response_id

            function_calls = self._collect_tool_calls(response_parts)
            if not function_calls or not self.tools:
                break

            if tool_iterations >= self.max_tool_iterations:
                logger.warning(
                    "[%s] Reached max_tool_iterations=%d, stop tool loop",
                    self.name,
                    self.max_tool_iterations,
                )
                break

            if ctx is None:
                raise RuntimeError(f"LLM node '{self.name}' requires InvocationContext for tool execution")

            tool_iterations += 1
            tool_messages, round_last_tool_response = await self._execute_tool_round(
                function_calls=function_calls,
                ctx=ctx,
            )
            if tool_messages:
                conversation_messages.extend(tool_messages)
                messages_update.extend(tool_messages)
            if round_last_tool_response:
                last_tool_response = round_last_tool_response

        result: dict[str, Any] = {
            STATE_KEY_MESSAGES: messages_update,
            STATE_KEY_LAST_RESPONSE: final_response_text,
            STATE_KEY_NODE_RESPONSES: {
                self.name: final_response_text
            },
        }

        if final_response_id:
            result[STATE_KEY_LAST_RESPONSE_ID] = final_response_id
        if last_tool_response:
            result[STATE_KEY_LAST_TOOL_RESPONSE] = last_tool_response

        result.update(clear_update)
        return result
