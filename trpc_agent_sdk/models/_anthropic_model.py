# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Anthropic model implementation module.

This module provides the AnthropicModel class which implements the BaseModel interface
for interacting with Anthropic's Claude API. It supports both streaming and non-streaming
responses, tool calls, and various Anthropic-specific features.
"""

import base64
import json
from enum import Enum
from typing import Any
from typing import AsyncGenerator
from typing import Dict
from typing import List
from typing import Literal
from typing import Optional
from typing_extensions import override

from anthropic import AsyncAnthropic
from anthropic import types as anthropic_types

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import GenerateContentResponseUsageMetadata
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import Tool

from . import _constants as const
from ._llm_model import LLMModel
from ._llm_request import LlmRequest
from ._llm_response import LlmResponse
from ._registry import register_model

_VALID_ROLES: set[str] = {const.USER, const.ASSISTANT, const.MODEL, const.SYSTEM}


class _FinishReason(str, Enum):
    """Reasons why model generation finished."""

    STOP = "stop"
    MAX_TOKENS = "max_tokens"
    ERROR = "error"
    TOOL_USE = "tool_use"


class _ApiParamsKey(str, Enum):
    """API parameter keys for Anthropic API calls."""

    MODEL = const.MODEL
    MESSAGES = "messages"
    STREAM = "stream"
    MAX_TOKENS = "max_tokens"
    TEMPERATURE = "temperature"
    TOP_P = "top_p"
    TOP_K = "top_k"
    STOP_SEQUENCES = "stop_sequences"
    SYSTEM = "system"
    TOOLS = "tools"
    TOOL_CHOICE = "tool_choice"
    THINKING = "thinking"


@register_model(model_name="AnthropicModel", supported_models=[r"claude-.*"])
class AnthropicModel(LLMModel):
    """Anthropic model implementation using the abstract model interface.

    This class provides integration with Anthropic's Claude API, supporting features like:
    - Streaming and non-streaming responses
    - Tool/function calling (native Anthropic format)
    - Vision API for image inputs
    - Default configuration via generate_content_config

    Args:
        model_name: The Anthropic model name (e.g., "claude-3-5-sonnet-20241022")
        filters_name: Optional list of filter names to apply
        generate_content_config: Default configuration for all requests. This config
                                will be used as the base, with per-request configs
                                overriding specific fields.
        **kwargs: Additional arguments passed to parent LLMModel class
                 (e.g., api_key, base_url, etc.)
    """

    def __init__(
        self,
        model_name: str,
        filters_name: Optional[list[str]] = None,
        generate_content_config: Optional[GenerateContentConfig] = None,
        **kwargs,
    ):
        super().__init__(model_name, filters_name, **kwargs)

        # Extract Anthropic-specific config
        self.client_args = kwargs.get(const.CLIENT_ARGS, {})

        # Default generation config that can be overridden per request
        self.generate_content_config = generate_content_config

    def _create_async_client(self):
        """Create a new async client instance."""

        # Disable httpx logging to prevent HTTP request logs
        import logging

        logging.getLogger("httpx").setLevel(logging.WARNING)

        return AsyncAnthropic(
            api_key=self._api_key,
            max_retries=0,  # disable retries
            base_url=self._base_url if self._base_url else None,
            **self.client_args,
        )

    @override
    def validate_request(self, request: LlmRequest) -> None:
        """Validate the request before processing."""

        if not request.contents:
            raise ValueError("At least one content is required")

        # Validate content structure
        for content in request.contents:
            if not content.parts:
                raise ValueError("Content must have at least one part")

            # Check if content has valid role
            if content.role and content.role not in _VALID_ROLES:
                raise ValueError(f"Invalid content role: {content.role}")

            # Validate parts have content
            has_content = False
            for part in content.parts:
                condition_iter = [
                    part.text,
                    part.function_call,
                    part.function_response,
                    part.code_execution_result,
                    part.executable_code,
                    part.inline_data,
                ]
                has_content = any(condition_iter)
                if has_content:
                    break

            if not has_content:
                raise ValueError("Content parts must have text, function_call, function_response, "
                                 "code_execution_result, executable_code, or inline_data")

    def _to_claude_role(self, role: Optional[str]) -> Literal["user", "assistant"]:
        """Convert role to Claude format."""
        if role in [const.MODEL, const.ASSISTANT]:
            return "assistant"
        return "user"

    def _is_image_part(self, part: Part) -> bool:
        """Check if a part contains image data."""
        return (part.inline_data and part.inline_data.mime_type and part.inline_data.mime_type.startswith("image"))

    def _part_to_message_block(
        self, part: Part
    ) -> (anthropic_types.TextBlockParam
          | anthropic_types.ImageBlockParam
          | anthropic_types.ToolUseBlockParam
          | anthropic_types.ToolResultBlockParam):
        """Convert a Part to an Anthropic message block."""
        if part.text:
            return anthropic_types.TextBlockParam(text=part.text, type="text")
        elif part.function_call:
            assert part.function_call.name

            return anthropic_types.ToolUseBlockParam(
                id=part.function_call.id or "",
                name=part.function_call.name,
                input=part.function_call.args,
                type="tool_use",
            )
        elif part.function_response:
            content = ""
            response_data = part.function_response.response

            # Handle response with content array
            if "content" in response_data and response_data["content"]:
                content_items = []
                for item in response_data["content"]:
                    if isinstance(item, dict):
                        # Handle text content blocks
                        if item.get("type") == "text" and "text" in item:
                            content_items.append(item["text"])
                        else:
                            # Handle other structured content
                            content_items.append(str(item))
                    else:
                        content_items.append(str(item))
                content = "\n".join(content_items) if content_items else ""
            # Handle traditional result format
            elif "result" in response_data and response_data["result"]:
                content = str(response_data["result"])
            else:
                # Handle simple response format
                content = json.dumps(response_data, ensure_ascii=False)

            return anthropic_types.ToolResultBlockParam(
                tool_use_id=part.function_response.id or "",
                type="tool_result",
                content=content,
                is_error=False,
            )
        elif self._is_image_part(part):
            data = base64.b64encode(part.inline_data.data).decode()  # type: ignore
            return anthropic_types.ImageBlockParam(
                type="image",
                source=dict(
                    type="base64",
                    media_type=part.inline_data.mime_type,
                    data=data  # type: ignore
                ),
            )
        elif part.executable_code:
            return anthropic_types.TextBlockParam(
                type="text",
                text="Code:```python\n" + part.executable_code.code + "\n```",
            )
        elif part.code_execution_result:
            return anthropic_types.TextBlockParam(
                text="Execution Result:```code_output\n" + part.code_execution_result.output + "\n```",
                type="text",
            )

        raise NotImplementedError(f"Not supported yet: {part}")

    def _format_messages(self, request: LlmRequest) -> List[anthropic_types.MessageParam]:
        """Format contents for Anthropic API as messages."""
        formatted_messages = []

        # Convert Contents to Anthropic message format
        for content in request.contents:
            # Determine role
            role = self._to_claude_role(content.role)

            message_blocks = []
            for part in content.parts:  # type: ignore
                # Image data is not supported in Claude for model turns
                if self._is_image_part(part) and role == "assistant":
                    logger.warning("Image data is not supported in Claude for model turns.")
                    continue

                message_blocks.append(self._part_to_message_block(part))

            if message_blocks:
                formatted_messages.append(anthropic_types.MessageParam(role=role, content=message_blocks))

        return formatted_messages

    def _parse_finish_reason(self, stop_reason: Optional[str]) -> _FinishReason:
        """Convert Anthropic stop reason to our enum."""
        if stop_reason in ["end_turn", "stop_sequence"]:
            return _FinishReason.STOP
        elif stop_reason == "max_tokens":
            return _FinishReason.MAX_TOKENS
        elif stop_reason == "tool_use":
            return _FinishReason.TOOL_USE
        return _FinishReason.ERROR

    def _update_type_string(self, value_dict: dict[str, Any]):
        """Updates 'type' field to expected JSON schema format."""
        if "type" in value_dict:
            value_dict["type"] = value_dict["type"].lower()

        if "items" in value_dict:
            # 'type' field could exist for items as well
            self._update_type_string(value_dict["items"])

            if "properties" in value_dict["items"]:
                # Recursively traverse each property
                for _, value in value_dict["items"]["properties"].items():
                    self._update_type_string(value)

    def _function_declaration_to_tool_param(self, function_declaration) -> anthropic_types.ToolParam:
        """Convert a function declaration to an Anthropic tool param."""
        assert function_declaration.name

        # Use parameters_json_schema if available, otherwise convert from parameters
        if hasattr(function_declaration, "parameters_json_schema") and function_declaration.parameters_json_schema:
            input_schema = function_declaration.parameters_json_schema
        else:
            properties = {}
            required_params = []
            if function_declaration.parameters:
                if function_declaration.parameters.properties:
                    for key, value in function_declaration.parameters.properties.items():
                        value_dict = value.model_dump(exclude_none=True)
                        self._update_type_string(value_dict)
                        properties[key] = value_dict
                if function_declaration.parameters.required:
                    required_params = function_declaration.parameters.required

            input_schema = {
                "type": "object",
                "properties": properties,
            }
            if required_params:
                input_schema["required"] = required_params

        return anthropic_types.ToolParam(
            name=function_declaration.name,
            description=function_declaration.description or "",
            input_schema=input_schema,
        )

    def _convert_tools_to_anthropic_format(self, tools: List[Tool]) -> List[anthropic_types.ToolParam]:
        """Convert tools to Anthropic tools format."""
        anthropic_tools = []

        for tool in tools:
            # Handle Tool objects with function_declarations
            if tool.function_declarations:
                for func_decl in tool.function_declarations:
                    anthropic_tools.append(self._function_declaration_to_tool_param(func_decl))

        return anthropic_tools

    def _create_streaming_tool_call_response(
        self,
        accumulated_tool_uses: list[dict],
        delta_json: str,
        streaming_tool_names: Optional[set] = None,
    ) -> Optional[LlmResponse]:
        """Create a streaming tool call response with partial arguments.

        This method creates LlmResponse events for streaming tool call arguments,
        allowing real-time display of tool call arguments as they are generated.

        Args:
            accumulated_tool_uses: The accumulated tool uses so far (each dict has
                                   'id', 'name', 'accumulated_input')
            delta_json: The delta JSON string from this chunk
            streaming_tool_names: Set of tool names that should receive streaming events.
                                 If None, all tools receive streaming events.

        Returns:
            LlmResponse with partial tool call information, or None if no valid data
        """
        if not accumulated_tool_uses:
            return None

        # Get the current (last) tool being streamed
        current_tool = accumulated_tool_uses[-1]
        tool_name = current_tool.get("name", "")
        tool_id = current_tool.get("id", "")
        accumulated_args = current_tool.get("accumulated_input", "")

        if not tool_name:
            return None

        # Only process tools that are in the streaming_tool_names set
        if streaming_tool_names is not None and tool_name not in streaming_tool_names:
            return None

        # Create function call part with delta only
        # Agent layer accumulates deltas to build complete JSON
        function_part = Part.from_function_call(name=tool_name, args={const.TOOL_STREAMING_ARGS: delta_json})

        if tool_id:
            function_part.function_call.id = tool_id  # type: ignore

        streaming_content = Content(parts=[function_part], role=const.MODEL)
        return LlmResponse(
            content=streaming_content,
            partial=True,
        )

    def _content_block_to_part(self, content_block: anthropic_types.ContentBlock) -> Part:
        """Convert an Anthropic content block to a Part."""
        if isinstance(content_block, anthropic_types.TextBlock):
            return Part.from_text(text=content_block.text)
        if isinstance(content_block, anthropic_types.ToolUseBlock):
            assert isinstance(content_block.input, dict)
            part = Part.from_function_call(name=content_block.name, args=content_block.input)
            part.function_call.id = content_block.id  # type: ignore
            return part
        # Handle thinking blocks (extended thinking feature)
        if isinstance(content_block, anthropic_types.ThinkingBlock):
            part = Part.from_text(text=content_block.thinking)
            part.thought = True  # Mark as thinking content
            return part
        # Handle redacted thinking blocks
        if isinstance(content_block, anthropic_types.RedactedThinkingBlock):
            part = Part.from_text(text="[Thinking content redacted]")
            part.thought = True  # Mark as thinking content
            return part
        raise NotImplementedError(f"Not supported yet: {type(content_block)}")

    def _message_to_llm_response(self, message: anthropic_types.Message) -> LlmResponse:
        """Convert an Anthropic message to LlmResponse."""
        logger.info("Received response from Anthropic Claude.")
        logger.debug(
            "Claude response: %s",
            message.model_dump_json(indent=2, exclude_none=True),
        )

        return LlmResponse(
            content=Content(
                role=const.MODEL,
                parts=[self._content_block_to_part(cb) for cb in message.content],
            ),
            usage_metadata=GenerateContentResponseUsageMetadata(
                prompt_token_count=message.usage.input_tokens,
                candidates_token_count=message.usage.output_tokens,
                total_token_count=(message.usage.input_tokens + message.usage.output_tokens),
            ),
        )

    def _merge_configs(self, request_config: Optional[GenerateContentConfig]) -> GenerateContentConfig:
        """Merge the default generate_content_config with the request config."""
        # If no request config provided, use default config if available
        if not request_config:
            if self.generate_content_config:
                return self.generate_content_config.model_copy(deep=True)
            return GenerateContentConfig()

        # If no default config, return request config as is
        if not self.generate_content_config:
            return request_config

        # Get explicitly set fields from request_config
        request_set_fields = request_config.model_fields_set

        # Get explicitly set fields from default config
        default_set_fields = self.generate_content_config.model_fields_set

        # Set default values on request_config for fields that are not already set
        for field_name in default_set_fields:
            if field_name not in request_set_fields:
                # Only set if not already set in request_config
                default_value = getattr(self.generate_content_config, field_name)
                setattr(request_config, field_name, default_value)
                # Update model_fields_set to reflect that this field is now set
                request_config.model_fields_set.add(field_name)

        return request_config

    def _log_unsupported_config_options(self, config: GenerateContentConfig) -> None:
        """Log warnings for configuration options that are not supported in Anthropic."""
        unsupported_options = []

        if config.frequency_penalty is not None:
            unsupported_options.append("frequency_penalty")
        if config.presence_penalty is not None:
            unsupported_options.append("presence_penalty")
        if config.seed is not None:
            unsupported_options.append("seed")
        if config.response_logprobs is not None:
            unsupported_options.append("response_logprobs")
        if config.logprobs is not None:
            unsupported_options.append("logprobs")
        if config.candidate_count is not None and config.candidate_count > 1:
            unsupported_options.append("candidate_count > 1")
        if config.safety_settings:
            unsupported_options.append("safety_settings")
        if config.cached_content:
            unsupported_options.append("cached_content")
        if config.response_modalities:
            unsupported_options.append("response_modalities")
        if config.media_resolution:
            unsupported_options.append("media_resolution")
        if config.speech_config:
            unsupported_options.append("speech_config")
        if config.audio_timestamp:
            unsupported_options.append("audio_timestamp")
        if config.automatic_function_calling:
            unsupported_options.append("automatic_function_calling")

        if unsupported_options:
            logger.warning(
                "The following configuration options are not supported in Anthropic models and will be ignored: %s",
                ', '.join(unsupported_options),
            )

    async def _generate_single(
        self,
        api_params: Dict,
        request: LlmRequest,
        ctx: InvocationContext | None = None,
    ) -> LlmResponse:
        """Generate a single response (non-streaming)."""
        client = self._create_async_client()
        try:
            response = await client.messages.create(**api_params)

            return self._message_to_llm_response(response)
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Anthropic API error: %s", ex)
            return LlmResponse(content=None,
                               error_code="API_ERROR",
                               error_message=str(ex),
                               custom_metadata={"error": str(ex)})
        finally:
            await client.close()

    async def _generate_stream(
        self,
        api_params: Dict,
        request: LlmRequest,
        ctx: InvocationContext | None = None,
    ) -> AsyncGenerator[LlmResponse, None]:
        """Generate streaming responses."""
        accumulated_content = ""
        accumulated_thinking = ""
        # Track tool uses with their accumulated input for streaming
        # Each entry: {"id": str, "name": str, "index": int, "accumulated_input": str}
        accumulated_tool_uses: list[dict] = []
        # Map content block index to tool use index in accumulated_tool_uses
        block_index_to_tool_index: dict[int, int] = {}
        last_usage = None

        # Get the set of tool names that should stream
        streaming_tool_names = getattr(request, 'streaming_tool_names', None) or set()

        client = self._create_async_client()
        try:
            logger.debug("Anthropic invoke with params: %s", api_params)
            logger.debug("Anthropic invoke with params: %s", api_params)

            async with client.messages.stream(**api_params) as stream:
                async for event in stream:
                    logger.debug(f"Anthropic event: {event}")
                    # Handle content block delta events
                    if hasattr(event, "type") and event.type == "content_block_delta":
                        delta = event.delta
                        if hasattr(delta, "type") and delta.type == "text_delta":
                            text = delta.text
                            accumulated_content += text

                            # Yield partial response
                            content_part = Part.from_text(text=text)
                            content_part.thought = False  # Regular text content
                            partial_content = Content(parts=[content_part], role=const.MODEL)
                            yield LlmResponse(content=partial_content, partial=True)

                        elif hasattr(delta, "type") and delta.type == "thinking_delta":
                            # Handle thinking content deltas
                            thinking_text = delta.thinking
                            accumulated_thinking += thinking_text

                            # Yield partial thinking response
                            content_part = Part.from_text(text=thinking_text)
                            content_part.thought = True  # Mark as thinking content
                            partial_content = Content(parts=[content_part], role=const.MODEL)
                            yield LlmResponse(content=partial_content, partial=True)

                        elif hasattr(delta, "type") and delta.type == "input_json_delta":
                            # Tool use input streaming
                            partial_json = delta.partial_json
                            block_index = event.index if hasattr(event, "index") else -1

                            # Find the corresponding tool use and accumulate
                            if block_index in block_index_to_tool_index:
                                tool_idx = block_index_to_tool_index[block_index]
                                accumulated_tool_uses[tool_idx]["accumulated_input"] += partial_json

                                # Yield streaming tool call event if enabled
                                if streaming_tool_names:
                                    streaming_event = self._create_streaming_tool_call_response(
                                        accumulated_tool_uses,
                                        partial_json,
                                        streaming_tool_names,
                                    )
                                    if streaming_event:
                                        yield streaming_event

                    # Handle content block start events (for tool use)
                    elif hasattr(event, "type") and event.type == "content_block_start":
                        if hasattr(event, "content_block"):
                            content_block = event.content_block
                            if isinstance(content_block, anthropic_types.ToolUseBlock):
                                block_index = event.index if hasattr(event, "index") else len(accumulated_tool_uses)
                                tool_entry = {
                                    "id": content_block.id,
                                    "name": content_block.name,
                                    "index": block_index,
                                    "accumulated_input": "",
                                }
                                tool_idx = len(accumulated_tool_uses)
                                accumulated_tool_uses.append(tool_entry)
                                block_index_to_tool_index[block_index] = tool_idx

                    # Handle message delta events (for usage)
                    elif hasattr(event, "type") and event.type == "message_delta":
                        if hasattr(event, "usage"):
                            usage = event.usage
                            last_usage = GenerateContentResponseUsageMetadata(
                                prompt_token_count=0,
                                candidates_token_count=usage.output_tokens,
                                total_token_count=usage.output_tokens,
                            )

            # Get the final message for complete usage stats
            final_message = await stream.get_final_message()

            # Yield final complete response
            final_parts = []

            # Add thinking content first if present
            if accumulated_thinking:
                thinking_part = Part.from_text(text=accumulated_thinking)
                thinking_part.thought = True
                final_parts.append(thinking_part)

            # Add regular content
            if accumulated_content:
                content_part = Part.from_text(text=accumulated_content)
                content_part.thought = False
                final_parts.append(content_part)

            # Add tool uses from the final message (not from accumulated events)
            # This ensures we get the complete tool_use blocks with all input populated
            for content_block in final_message.content:
                if isinstance(content_block, anthropic_types.ToolUseBlock):
                    part = Part.from_function_call(name=content_block.name, args=content_block.input)
                    part.function_call.id = content_block.id  # type: ignore
                    final_parts.append(part)

            final_content = None
            if final_parts:
                final_content = Content(parts=final_parts, role=const.MODEL)

            final_usage = GenerateContentResponseUsageMetadata(
                prompt_token_count=final_message.usage.input_tokens,
                candidates_token_count=final_message.usage.output_tokens,
                total_token_count=(final_message.usage.input_tokens + final_message.usage.output_tokens),
            )

            yield LlmResponse(content=final_content,
                              usage_metadata=final_usage,
                              partial=False,
                              custom_metadata={"stream_complete": True})

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error in streaming response: %s", ex, exc_info=True)
            logger.error("Error in streaming response: %s", ex, exc_info=True)
            yield LlmResponse(
                content=None,
                error_code="STREAMING_ERROR",
                error_message=f"Error in streaming: {str(ex)}",
                partial=False,
                custom_metadata={"error": str(ex)},
            )
        finally:
            await client.close()

    @override
    async def _generate_async_impl(self,
                                   request: LlmRequest,
                                   stream: bool = False,
                                   ctx: InvocationContext | None = None) -> AsyncGenerator[LlmResponse, None]:
        """Generate content asynchronously."""
        self.validate_request(request)

        # Merge default config with request config
        merged_config = self._merge_configs(request.config)

        # Update request with merged config
        request.config = merged_config

        # Prepare Anthropic API parameters
        messages = self._format_messages(request)

        # Debug log the formatted messages
        logger.debug("Formatted messages for Anthropic API: %s", json.dumps([m for m in messages], indent=2))

        api_params = {
            _ApiParamsKey.MODEL: self._model_name,
            _ApiParamsKey.MESSAGES: messages,
            _ApiParamsKey.MAX_TOKENS: 8192,  # Default max tokens
        }

        # Add configuration parameters
        try:
            if request.config:
                # Log warnings for unsupported configuration options
                self._log_unsupported_config_options(request.config)

                # System instruction
                if request.config.system_instruction:
                    api_params[_ApiParamsKey.SYSTEM] = str(request.config.system_instruction)

                # Max tokens
                if request.config.max_output_tokens:
                    api_params[_ApiParamsKey.MAX_TOKENS] = request.config.max_output_tokens

                # Temperature
                if request.config.temperature is not None:
                    api_params[_ApiParamsKey.TEMPERATURE] = request.config.temperature

                # Top P
                if request.config.top_p is not None:
                    api_params[_ApiParamsKey.TOP_P] = request.config.top_p

                # Top K (Anthropic-specific)
                if request.config.top_k is not None:
                    api_params[_ApiParamsKey.TOP_K] = request.config.top_k

                # Stop sequences
                if request.config.stop_sequences:
                    api_params[_ApiParamsKey.STOP_SEQUENCES] = request.config.stop_sequences

                # Handle tools
                if request.config.tools:
                    converted_tools = self._convert_tools_to_anthropic_format(request.config.tools)  # type: ignore
                    if converted_tools:
                        api_params[_ApiParamsKey.TOOLS] = converted_tools
                        api_params[_ApiParamsKey.TOOL_CHOICE] = anthropic_types.ToolChoiceAutoParam(type="auto")

                # Handle thinking config (Anthropic extended thinking)
                if request.config.thinking_config:
                    thinking_config = request.config.thinking_config

                    # Only enable thinking if include_thoughts is True
                    if thinking_config.include_thoughts:
                        # Determine budget tokens
                        budget_tokens = 2048  # Default budget

                        if thinking_config.thinking_budget is not None:
                            # thinking_budget: 0 is DISABLED, -1 is AUTOMATIC, >0 is specific token budget
                            if thinking_config.thinking_budget == 0:
                                # Explicitly disabled, skip thinking
                                logger.debug("Thinking explicitly disabled via thinking_budget=0")
                            elif thinking_config.thinking_budget == -1:
                                # Automatic mode - use default budget
                                budget_tokens = 2048
                            else:
                                # Use the specified budget
                                budget_tokens = thinking_config.thinking_budget

                        # Anthropic requires minimum 1024 tokens for thinking
                        if budget_tokens < 1024:
                            logger.warning(
                                "Thinking budget %s is below Anthropic's minimum of 1024 tokens. Adjusting to 1024.",
                                budget_tokens)
                            budget_tokens = 1024

                        # Only set thinking parameter if budget is positive
                        if thinking_config.thinking_budget != 0:
                            # Set the thinking parameter for Anthropic API
                            api_params[_ApiParamsKey.THINKING] = {"type": "enabled", "budget_tokens": budget_tokens}
                            logger.debug("Enabled Anthropic extended thinking with budget: %s tokens", budget_tokens)

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error in Anthropic API parameters: %s", ex, exc_info=True)
            raise ex

        try:
            if stream:
                async for response in self._generate_stream(api_params, request, ctx):
                    yield response
            else:
                response = await self._generate_single(api_params, request, ctx)
                yield response
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Anthropic API error: %s", ex)
            # Create error response
            yield LlmResponse(content=None,
                              error_code="API_ERROR",
                              error_message=str(ex),
                              custom_metadata={"error": str(ex)})
