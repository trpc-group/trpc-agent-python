# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""OpenAI model implementation module.

This module provides the OpenAIModel class which implements the BaseModel interface
for interacting with OpenAI's API. It supports both streaming and non-streaming
responses, tool calls, and various OpenAI-specific features.
"""

import base64
import json
import uuid
from enum import Enum
from typing import Any
from typing import AsyncGenerator
from typing import Dict
from typing import List
from typing import Optional
from typing_extensions import override

import openai
from pydantic import BaseModel

from trpc_agent_sdk.common import checkenum
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import GenerateContentResponseUsageMetadata
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Tool

from . import _constants as const
from ._llm_model import LLMModel
from ._llm_request import LlmRequest
from ._llm_response import LlmResponse
from ._registry import register_model
from .tool_prompt import ToolPromptFactory
from .tool_prompt import get_factory
from .tool_prompt._base import ToolPrompt

VALID_ROLES: set[str] = {const.USER, const.ASSISTANT, const.MODEL, const.SYSTEM}


class ToolCall(BaseModel):
    """Represents a tool call made by the model."""

    id: str
    name: str
    arguments: Dict[str, Any]
    thought_signature: Optional[str] = None


class FinishReason(str, Enum):
    """Reasons why model generation finished."""

    STOP = "stop"
    LENGTH = "length"
    ERROR = "error"
    TOOL_CALLS = const.TOOL_CALLS


class ToolKey(str, Enum):
    """Tool keys for tool calls."""

    ID = "id"
    TYPE = "type"
    NAME = "name"
    FUNCTION = "function"
    ARGUMENTS = "arguments"
    THOUGHT_SIGNATURE = "thought_signature"
    PROVIDER_SPECIFIC_FIELDS = "provider_specific_fields"


class ApiParamsKey(str, Enum):
    """Tool keys for tool calls."""

    MODEL = const.MODEL
    MESSAGES = "messages"
    STREAM = "stream"
    MAX_TOKENS = "max_tokens"
    TEMPERATURE = "temperature"
    TOP_P = "top_p"
    STOP = "stop"
    TOOLS = "tools"
    TOOL_CHOICE = "tool_choice"
    STREAM_OPTS = "stream_options"
    INCLUDE_USAGE = "include_usage"
    # Additional OpenAI parameters for better configuration support
    FREQUENCY_PENALTY = "frequency_penalty"
    PRESENCE_PENALTY = "presence_penalty"
    SEED = "seed"
    LOGPROBS = "logprobs"
    TOP_LOGPROBS = "top_logprobs"
    N = "n"
    RESPONSE_FORMAT = "response_format"
    MAX_COMPLETION_TOKENS = "max_completion_tokens"
    REASONING_EFFORT = "reasoning_effort"
    PARALLEL_TOOL_CALLS = "parallel_tool_calls"


@register_model(model_name="OpenAIModel", supported_models=[r"gpt-.*", r"o1-.*", r"deepseek-.*"])
class OpenAIModel(LLMModel):
    """OpenAI model implementation using the abstract model interface.

    This class provides integration with OpenAI's API, supporting features like:
    - Streaming and non-streaming responses
    - Tool/function calling (both native and via text prompts)
    - Vision API for image inputs
    - Default configuration via generate_content_config

    Args:
        model_name: The OpenAI model name (e.g., "gpt-4", "gpt-3.5-turbo")
        filters_name: Optional list of filter names to apply
        add_tools_to_prompt: If True, tools are added to the system prompt as text
                           instead of using OpenAI's native function calling
        tool_prompt: Tool prompt format to use when add_tools_to_prompt=True
                    (default: "xml")
        generate_content_config: Default configuration for all requests. This config
                                will be used as the base, with per-request configs
                                overriding specific fields. Useful for maintaining
                                consistent model behavior across multiple calls.
        **kwargs: Additional arguments passed to parent LLMModel class
                 (e.g., api_key, base_url, etc.)

    Example:
        >>> # Create model with default config
        >>> default_config = GenerateContentConfig(
        ...     temperature=0.7,
        ...     max_output_tokens=1000,
        ...     top_p=0.9
        ... )
        >>> model = OpenAIModel(
        ...     model_name="gpt-4",
        ...     api_key="your-api-key",
        ...     generate_content_config=default_config
        ... )
        >>>
        >>> # Request without config uses defaults
        >>> request1 = LlmRequest(
        ...     contents=[Content(parts=[Part.from_text(text="Hello")])],
        ...     config=None  # Will use temperature=0.7, max_output_tokens=1000, etc.
        ... )
        >>>
        >>> # Request with partial config overrides specific fields
        >>> request2 = LlmRequest(
        ...     contents=[Content(parts=[Part.from_text(text="Hello")])],
        ...     config=GenerateContentConfig(temperature=0.3)  # Override only temperature
        ... )
    """

    def __init__(
        self,
        model_name: str,
        filters_name: Optional[list[str]] = None,
        add_tools_to_prompt: bool = False,
        tool_prompt: str = "xml",
        generate_content_config: Optional[GenerateContentConfig] = None,
        **kwargs,
    ):
        super().__init__(model_name, filters_name, **kwargs)

        # Extract OpenAI-specific config
        self.organization: str = kwargs.get(const.ORGANIZATION, "")
        self.client_args = kwargs.get(const.CLIENT_ARGS, {})

        # Tool prompt configuration
        self.add_tools_to_prompt = add_tools_to_prompt
        self.tool_prompt = tool_prompt

        # Default generation config that can be overridden per request
        self.generate_content_config = generate_content_config
        # Optional hard cap for tool-response payload injected into model
        # context. Disabled by default; callers (e.g. OpenClaw) can opt in.
        self._tool_response_clip_chars = int(kwargs.get("tool_response_clip_chars", 0) or 0)

        # Validate tool_prompt parameter
        if isinstance(self.tool_prompt, str):
            # Validate that the string is registered in factory
            factory: ToolPromptFactory = get_factory()
            try:
                factory.create(self.tool_prompt)  # Test creation to validate
            except Exception as ex:  # pylint: disable=broad-except
                raise ValueError(f"Invalid tool_prompt string '{self.tool_prompt}': {ex}")
        elif not (isinstance(self.tool_prompt, type) and issubclass(self.tool_prompt, ToolPrompt)):
            raise ValueError(f"tool_prompt must be a string or ToolPrompt class, got {type(self.tool_prompt)}")

    def _create_async_client(self):
        """Create a new async client instance."""

        # Disable httpx logging to prevent HTTP request logs
        import logging

        logging.getLogger("httpx").setLevel(logging.WARNING)

        return openai.AsyncOpenAI(
            api_key=self._api_key,
            max_retries=0,  # disable retries
            organization=self.organization,
            base_url=self._base_url,
            **self.client_args,
        )

    def _create_tool_prompt(self) -> ToolPrompt:
        """Create a tool prompt instance from the blueprint."""
        if isinstance(self.tool_prompt, str):
            # Get tool prompt from factory
            factory: ToolPromptFactory = get_factory()
            return factory.create(self.tool_prompt)
        return self.tool_prompt()

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
            if content.role and content.role not in VALID_ROLES:
                raise ValueError(f"Invalid content role: {content.role}")

            # Validate parts have content
            has_content = False
            for part in content.parts:
                condition_iter = [
                    part.text, part.function_call, part.function_response, part.code_execution_result,
                    part.executable_code, part.inline_data
                ]
                has_content = any(condition_iter)
                if has_content:
                    break

            if not has_content:
                raise ValueError("Content parts must have text, function_call, function_response, "
                                 "code_execution_result, executable_code, or inline_data")

    def _get_part_thought_signature(self, part: Part) -> str:
        """Get thought_signature from Part as str; return dummy if missing.
        See https://ai.google.dev/gemini-api/docs/thought-signatures (Gemini 3+).
        """
        raw = getattr(part, "thought_signature", None)
        if not raw:
            return base64.b64encode(b"skip_thought_signature_validator").decode("utf-8")
        if isinstance(raw, bytes):
            return base64.b64encode(raw).decode("utf-8")
        return raw

    def _set_part_thought_signature(self, function_part: Part, thought_signature: Optional[str]) -> None:
        """Attach thought_signature to Part for next request.
        Store as bytes to match Part schema and avoid serialization warnings.
        """
        if not thought_signature:
            return
        sig = thought_signature
        try:
            setattr(
                function_part,
                "thought_signature",
                base64.b64decode(sig) if isinstance(sig, str) else sig,
            )
        except Exception:  # pylint: disable=broad-except
            setattr(
                function_part,
                "thought_signature",
                sig.encode("utf-8") if isinstance(sig, str) else sig,
            )

    def _format_messages(self, request: LlmRequest) -> List[Dict[str, Any]]:
        """Format contents for OpenAI API as messages."""
        formatted_messages = []

        # Add system message if provided in config
        system_text = ""
        if request.config and request.config.system_instruction:
            # Convert system_instruction to string if it's not already
            system_text = str(request.config.system_instruction)

        # Add tool prompt to system message if enabled and tools are available
        if self.add_tools_to_prompt and request.config and request.config.tools:
            tool_prompt = self._create_tool_prompt()
            tool_prompt_str = tool_prompt.build_prompt(request.config.tools)  # type: ignore
            if tool_prompt_str:
                if system_text:
                    system_text += f"\n\n{tool_prompt_str}"
                else:
                    system_text = tool_prompt_str

        # Add system message if we have any system content
        if system_text:
            request.config.system_instruction = system_text  # type: ignore
            formatted_messages.append({const.ROLE: const.SYSTEM, const.CONTENT: system_text})

        # Convert Contents to OpenAI message format
        for content in request.contents:
            # Determine role - map different roles for OpenAI compatibility
            role = content.role
            if role == const.MODEL:
                role = const.ASSISTANT  # OpenAI uses const.ASSISTANT instead of const.MODEL
            elif not role:
                # Default role based on content type
                role = const.USER  # Default to user if no role specified

            parts: list[Part] = content.parts  # type: ignore
            conditions_iter = [
                len(parts) == 1, parts[0].text, parts[0].function_call, parts[0].function_response,
                parts[0].code_execution_result, parts[0].executable_code, parts[0].inline_data
            ]
            # Handle different content structures
            if all(conditions_iter):
                # Simple text message
                formatted_messages.append({const.ROLE: role, const.CONTENT: parts[0].text})
            else:
                # Complex message with multiple parts or function calls/responses
                # Separate function responses from other content
                function_responses: list[FunctionResponse] = []
                text_parts = []
                image_parts = []
                tool_calls = []

                for part in parts:  # type: ignore
                    if part.text:
                        text_parts.append(part.text)
                    elif part.inline_data and part.inline_data.mime_type:
                        # Handle image data - convert to OpenAI vision format
                        base64_string = base64.b64encode(part.inline_data.data).decode("utf-8")  # type: ignore
                        data_uri = f"data:{part.inline_data.mime_type};base64,{base64_string}"
                        image_parts.append({"type": "image_url", "image_url": {"url": data_uri, "detail": "high"}})
                    elif part.function_call:
                        # Only convert function call to OpenAI tool call format if add_tools_to_prompt is disabled
                        if not self.add_tools_to_prompt:
                            tool_call = {
                                "id": getattr(part.function_call, "id", None) or f"call_{uuid.uuid4().hex[:24]}",
                                "type": "function",
                                "function": {
                                    "name":
                                    part.function_call.name,
                                    "arguments": (part.function_call.args if isinstance(part.function_call.args, str)
                                                  else json.dumps(part.function_call.args, ensure_ascii=False)),
                                },
                                "thought_signature": self._get_part_thought_signature(part),
                            }
                            tool_calls.append(tool_call)
                        # If add_tools_to_prompt is enabled, skip tool calls (they're handled via text prompts)
                    elif part.function_response:
                        # Collect function responses to be added as separate tool messages
                        function_responses.append(part.function_response)
                    elif part.code_execution_result:
                        # Handle code execution results - add to text parts
                        execution_result = f"{part.code_execution_result.outcome.value}"
                        if part.code_execution_result.output:
                            execution_result += f": {part.code_execution_result.output}"
                        result_text = f"CODE EXECUTION RESULT(DON'T SHOW THIS TEXT): {execution_result}\n"
                        text_parts.append(result_text)
                    elif part.executable_code:
                        # Handle executable code - add to text parts
                        if part.executable_code.language:
                            language = part.executable_code.language.value.lower()
                            code_text = f"```{language}\n{part.executable_code.code}\n```"
                        else:
                            code_text = f"```text\n{part.executable_code.code}\n```"
                        text_parts.append(code_text)

                # Handle function responses - role depends on add_tools_to_prompt setting
                if self.add_tools_to_prompt:
                    # merge tool responses to correctly mach the qa pair
                    content = ""
                    for func_response in function_responses:
                        content += f"invoke {func_response.name}, get rsp: "
                        if isinstance(func_response.response, dict):
                            content += json.dumps(func_response.response, ensure_ascii=False)
                        else:
                            content += str(func_response.response)
                        content += "\n"
                    content = self._clip_tool_response_text(content, "tool_response_merged")
                    if len(content) > 0:
                        tool_message = {
                            const.ROLE: const.USER,
                            const.CONTENT: content,
                        }
                        formatted_messages.append(tool_message)
                else:
                    for func_response in function_responses:
                        # Standard tool message format for OpenAI API
                        raw_text = (json.dumps(func_response.response, ensure_ascii=False) if isinstance(
                            func_response.response, dict) else str(func_response.response))
                        clipped_text = self._clip_tool_response_text(
                            raw_text,
                            getattr(func_response, "name", "tool"),
                        )
                        tool_message = {
                            const.ROLE: const.TOOL,
                            const.TOOL_CALL_ID: getattr(func_response, "id", "unknown"),
                            const.CONTENT: clipped_text,
                        }
                        formatted_messages.append(tool_message)

                # Create the main message (assistant/user) if it has content or tool calls
                if text_parts or image_parts or tool_calls:
                    message: dict = {const.ROLE: role}

                    # Handle content based on what we have
                    if image_parts or (text_parts and image_parts):
                        # Use array format for vision API when we have images
                        content_array = []

                        # Add text parts first
                        if text_parts:
                            content_array.append({"type": "text", "text": " ".join(text_parts)})

                        # Add image parts
                        content_array.extend(image_parts)

                        message[const.CONTENT] = content_array
                    elif text_parts:
                        # Simple text content when no images
                        message[const.CONTENT] = " ".join(text_parts)
                    else:
                        message[const.CONTENT] = ""  # Empty content if no text or tools

                    # Add tool calls if any (only when add_tools_to_prompt is disabled)
                    if tool_calls and not self.add_tools_to_prompt:
                        message[const.TOOL_CALLS] = tool_calls

                    formatted_messages.append(message)

        # Validate and fix message sequence for OpenAI compatibility
        return self._validate_and_fix_openai_messages(formatted_messages)

    def _validate_and_fix_openai_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Validate and fix message sequence to ensure OpenAI compatibility.

        OpenAI requires that assistant messages with tool_calls are immediately
        followed by tool messages responding to each tool_call_id.

        Args:
            messages: List of formatted messages

        Returns:
            List of validated and potentially fixed messages
        """
        if not messages:
            return messages

        fixed_messages = []
        pending_tool_calls = []  # Track tool calls that need responses

        for message in messages:
            role = message.get(const.ROLE, "")

            if role == const.ASSISTANT:
                # Check if this assistant message has tool_calls
                tool_calls = message.get(const.TOOL_CALLS, [])

                if tool_calls:
                    # If there are pending tool calls from a previous assistant message,
                    # we need to add dummy tool responses first
                    if pending_tool_calls:
                        logger.warning("Adding dummy tool responses for %s pending tool calls", len(pending_tool_calls))
                        for pending_call in pending_tool_calls:
                            dummy_response = {
                                const.ROLE: const.TOOL,
                                const.TOOL_CALL_ID: pending_call["id"],
                                const.CONTENT: json.dumps({
                                    "status": "completed",
                                    "note": "Tool call completed by system"
                                }),
                            }
                            fixed_messages.append(dummy_response)

                    # Add the current assistant message
                    fixed_messages.append(message)

                    # Update pending tool calls
                    pending_tool_calls = tool_calls
                else:
                    # Assistant message without tool calls
                    if pending_tool_calls:
                        # Need to add dummy responses for pending tool calls
                        logger.warning("Adding dummy tool responses for %s pending tool calls before assistant message",
                                       len(pending_tool_calls))
                        for pending_call in pending_tool_calls:
                            dummy_response = {
                                const.ROLE: const.TOOL,
                                const.TOOL_CALL_ID: pending_call["id"],
                                const.CONTENT: json.dumps({
                                    "status": "completed",
                                    "note": "Tool call completed by system"
                                }),
                            }
                            fixed_messages.append(dummy_response)
                        pending_tool_calls = []

                    # Add the assistant message
                    fixed_messages.append(message)

            elif role == const.TOOL:
                # Tool message - remove matching tool call from pending
                tool_call_id = message.get(const.TOOL_CALL_ID)
                if tool_call_id and pending_tool_calls:
                    # Remove the matching pending tool call
                    pending_tool_calls = [tc for tc in pending_tool_calls if tc["id"] != tool_call_id]

                fixed_messages.append(message)

            else:
                # User or system message
                if pending_tool_calls:
                    # Add dummy responses for any pending tool calls before user/system message
                    logger.warning("Adding dummy tool responses for %s pending tool calls before %s message",
                                   len(pending_tool_calls), role)
                    for pending_call in pending_tool_calls:
                        dummy_response = {
                            const.ROLE: const.TOOL,
                            const.TOOL_CALL_ID: pending_call["id"],
                            const.CONTENT: json.dumps({
                                "status": "completed",
                                "note": "Tool call completed by system"
                            }),
                        }
                        fixed_messages.append(dummy_response)
                    pending_tool_calls = []

                fixed_messages.append(message)

        # Handle any remaining pending tool calls at the end
        if pending_tool_calls:
            logger.warning("Adding dummy tool responses for %s remaining pending tool calls", len(pending_tool_calls))
            for pending_call in pending_tool_calls:
                dummy_response = {
                    const.ROLE: const.TOOL,
                    const.TOOL_CALL_ID: pending_call["id"],
                    const.CONTENT: json.dumps({
                        "status": "completed",
                        "note": "Tool call completed by system"
                    }),
                }
                fixed_messages.append(dummy_response)

        return fixed_messages

    def _parse_finish_reason(self, finish_reason: str) -> FinishReason:
        """Convert OpenAI finish reason to our enum."""
        if not checkenum(finish_reason, FinishReason):
            return FinishReason.ERROR
        return FinishReason(finish_reason)

    def _verify_text_content_in_delta_response(self, response: dict) -> bool:
        """Verify if the text content exists in the streaming response.

        Args:
            response (`dict`):
                The JSON-format response (After calling `model_dump` function)

        Returns:
            `bool`: If the text content exists and is not empty
        """
        choices: list[dict] = response.get(const.CHOICES, [{}])
        # Handle case where choices could be None (e.g., DeepSeek thinking events)
        if choices is None or not choices:
            return False
        delta: dict = choices[0].get(const.DELTA, {})
        if delta is None:
            return False

        # Check if regular content exists and is not None/empty
        content = delta.get(const.CONTENT)
        has_content = content is not None and content != ""

        # Check if reasoning content exists and is not None/empty
        reasoning_content = delta.get(const.REASONING_CONTENT)
        has_reasoning_content = reasoning_content is not None and reasoning_content != ""

        # Return True if either regular content or reasoning content exists
        return has_content or has_reasoning_content

    def _is_thinking_event(self, response: dict) -> bool:
        """Check if this is a thinking event from the model.

        Args:
            response (`dict`): The JSON-format response

        Returns:
            `bool`: True if this is a thinking event
        """
        return (response.get("object", "") == "stream_server.event"
                and response.get("event", {}).get("name", "") == "thinking")

    def _set_thinking(self, request: LlmRequest, http_options: dict):
        """Set thinking parameters from request config."""
        # Check if thinking config is available in the request
        if not request.config or not request.config.thinking_config:
            return

        thinking_config = request.config.thinking_config

        # Only set thinking parameters if include_thoughts is True
        if not thinking_config.include_thoughts:
            return

        if "extra_body" not in http_options:
            http_options["extra_body"] = {}
        processed_extra_body = http_options["extra_body"]

        # Enable thinking
        processed_extra_body[const.THINKING_ENABLED] = True

        # Set thinking budget if specified
        if thinking_config.thinking_budget is not None:
            max_output_tokens = request.config.max_output_tokens or 0
            if not max_output_tokens:
                raise ValueError("max_output_tokens must be set when thinking is enabled")

            # Handle different thinking budget values
            if thinking_config.thinking_budget == 0:
                # 0 means disabled, so don't enable thinking
                processed_extra_body[const.THINKING_ENABLED] = False
                return
            elif thinking_config.thinking_budget == -1:
                # -1 means automatic, let the model decide
                # Don't set thinking_tokens, let the model use its default
                pass
            elif thinking_config.thinking_budget > 0:
                # Positive value means specific token budget
                if thinking_config.thinking_budget <= max_output_tokens:
                    processed_extra_body[const.THINKING_TOKENS] = thinking_config.thinking_budget
                else:
                    raise ValueError(f"thinking_budget: {thinking_config.thinking_budget} "
                                     f"must be between 1024 and {max_output_tokens}")
            else:
                raise ValueError(f"Invalid thinking_budget value: {thinking_config.thinking_budget}. "
                                 "Must be 0 (disabled), -1 (automatic), or positive integer.")

    def _get_thinking_state(self, response: dict) -> int:
        """Get the thinking state from a thinking event.

        Args:
            response (`dict`): The JSON-format response

        Returns:
            `int`: The thinking state (0=start, 2=end, -1=not a thinking event)
        """
        if self._is_thinking_event(response):
            return response.get("event", {}).get("state", -1)
        return -1

    def _process_tool_call_delta(self, tool_call_delta: dict, accumulated_tool_calls: list[dict]) -> None:
        """Process a single tool call delta and update accumulated tool calls.

        Args:
            tool_call_delta (`dict`): The tool call delta to process
            accumulated_tool_calls (`list`): The list of accumulated tool calls
        """
        # Get the index of the tool call, handle None case
        index = tool_call_delta.get(const.INDEX, 0)
        if index is None:
            index = 0

        # Ensure we have enough slots in accumulated_tool_calls
        while len(accumulated_tool_calls) <= index:
            accumulated_tool_calls.append({
                ToolKey.ID: "",
                ToolKey.TYPE: ToolKey.FUNCTION,
                ToolKey.FUNCTION: {
                    ToolKey.NAME: "",
                    ToolKey.ARGUMENTS: ""
                },
                ToolKey.THOUGHT_SIGNATURE: "",
            })

        # Capture thought_signature from delta or provider_specific_fields for next-round pass-through
        thought_sig = tool_call_delta.get(ToolKey.THOUGHT_SIGNATURE)
        if not thought_sig and isinstance(tool_call_delta.get(ToolKey.PROVIDER_SPECIFIC_FIELDS), dict):
            thought_sig = tool_call_delta[ToolKey.PROVIDER_SPECIFIC_FIELDS].get(ToolKey.THOUGHT_SIGNATURE)
        if thought_sig:
            accumulated_tool_calls[index][ToolKey.THOUGHT_SIGNATURE] = thought_sig

        # Update the tool call with new information, preserving existing data
        # Only update if the field exists and is not None in the delta
        # For ID, preserve existing value if delta contains None or empty string (handles streaming inconsistencies)
        if ToolKey.ID in tool_call_delta:
            delta_id = tool_call_delta[ToolKey.ID]
            if delta_id is not None and delta_id != "":
                accumulated_tool_calls[index][ToolKey.ID] = delta_id
            # If tool_call_delta[ToolKey.ID] is None or empty string, keep the existing ID value

        if ToolKey.FUNCTION in tool_call_delta:
            function_delta = tool_call_delta[ToolKey.FUNCTION]
            if (ToolKey.NAME in function_delta and function_delta[ToolKey.NAME] is not None
                    and function_delta[ToolKey.NAME] != ""):
                accumulated_tool_calls[index][ToolKey.FUNCTION][ToolKey.NAME] = function_delta[ToolKey.NAME]
            # If function_delta[ToolKey.NAME] is None or empty, keep the existing name value
            if ToolKey.ARGUMENTS in function_delta and function_delta[ToolKey.ARGUMENTS] is not None:
                accumulated_tool_calls[index][ToolKey.FUNCTION][ToolKey.ARGUMENTS] += function_delta[ToolKey.ARGUMENTS]

    def _process_usage(self, chunk_dict: dict) -> Optional[GenerateContentResponseUsageMetadata]:
        """Process usage information from a chunk.

        Args:
            chunk_dict (`dict`): The chunk dictionary containing usage information

        Returns:
            `Optional[GenerateContentResponseUsageMetadata]`: The processed usage metadata or None if not available
        """
        usage_data = chunk_dict.get(const.USAGE)
        if usage_data is None:
            return None
        return GenerateContentResponseUsageMetadata(
            prompt_token_count=usage_data.get("prompt_tokens", 0),
            candidates_token_count=usage_data.get("completion_tokens", 0),
            total_token_count=usage_data.get("total_tokens", 0),
        )

    def _process_chunk_without_content(
        self, chunk_dict: dict, accumulated_tool_calls: list[dict]
    ) -> tuple[Optional[FinishReason], Optional[GenerateContentResponseUsageMetadata], dict[int, str]]:
        """Process a chunk that doesn't contain content.

        Args:
            chunk_dict (`dict`): The chunk dictionary to process
            accumulated_tool_calls (`list`): The list of accumulated tool calls

        Returns:
            Tuple of (finish_reason, usage_metadata, delta_arguments).
            delta_arguments maps tool index to this chunk's argument delta string.
        """
        choices = chunk_dict.get(const.CHOICES, [{}])
        # Handle case where choices could be None (e.g., DeepSeek thinking events)
        if choices is None or not choices:
            # Return early with only usage data if available
            usage = self._process_usage(chunk_dict)
            return None, usage, {}
        choice: dict = choices[0]

        delta: dict = choice.get(const.DELTA, {})
        if delta is None:
            return None, None, {}

        finish_reason = None

        # Handle finish reason
        if choice.get(const.FINISH_REASON):
            finish_reason = self._parse_finish_reason(choice[const.FINISH_REASON])

        # Handle usage
        usage = self._process_usage(chunk_dict)

        # Handle tool calls in chunks without content (this is where streaming tool calls happen)
        tool_calls_data = delta.get(const.TOOL_CALLS)

        # Track delta arguments from this chunk
        delta_arguments: dict[int, str] = {}

        if tool_calls_data and tool_calls_data is not None:
            for tool_call_delta in tool_calls_data:
                if tool_call_delta is None:
                    continue
                try:
                    # Extract delta arguments before processing (for delta mode)
                    index = tool_call_delta.get(const.INDEX, 0) or 0
                    function_delta = tool_call_delta.get(ToolKey.FUNCTION, {})
                    if function_delta and ToolKey.ARGUMENTS in function_delta:
                        delta_arg = function_delta.get(ToolKey.ARGUMENTS)
                        if delta_arg is not None:
                            delta_arguments[index] = delta_arg

                    self._process_tool_call_delta(tool_call_delta, accumulated_tool_calls)
                except Exception as ex:  # pylint: disable=broad-except
                    logger.error("Error processing tool call delta: %s", ex)
                    continue

        return finish_reason, usage, delta_arguments

    def _create_complete_tool_calls(self, accumulated_tool_calls: list[dict]) -> Optional[List[ToolCall]]:
        """Create ToolCall objects only for complete tool calls with valid data.

        Args:
            accumulated_tool_calls (`list`): The list of accumulated tool calls

        Returns:
            `Optional[List[ToolCall]]`: List of complete tool calls or None
        """
        if not accumulated_tool_calls:
            return None

        complete_tool_calls = []
        for i, tool_call_data in enumerate(accumulated_tool_calls):
            # Only create ToolCall if we have complete data
            function_map: dict = tool_call_data.get(ToolKey.FUNCTION, {})

            # Check if we have the essential fields (name and arguments)
            has_name = ToolKey.NAME in function_map and function_map[ToolKey.NAME]
            has_arguments = ToolKey.ARGUMENTS in function_map

            if has_name and has_arguments:
                try:
                    # Try to parse the arguments as JSON
                    arguments_str: str = function_map[ToolKey.ARGUMENTS].strip()
                    if arguments_str:  # Only parse non-empty arguments
                        arguments = json.loads(arguments_str)
                    else:
                        arguments = {}

                    # Handle missing or empty ID by generating a fallback
                    tool_call_id = tool_call_data.get(ToolKey.ID, "")
                    if not tool_call_id:
                        # Generate a fallback ID if missing
                        tool_call_id = f"call_{uuid.uuid4().hex[:24]}"
                        logger.warning("Generated fallback ID '%s' for tool call with missing ID", tool_call_id)

                    thought_sig = tool_call_data.get(ToolKey.THOUGHT_SIGNATURE) or None
                    logger.debug("Creating tool call: id=%s, name=%s, arguments=%s", tool_call_id,
                                 function_map[ToolKey.NAME], arguments)
                    complete_tool_calls.append(
                        ToolCall(
                            id=tool_call_id,
                            name=function_map[ToolKey.NAME],
                            arguments=arguments,
                            thought_signature=thought_sig,
                        ))
                except json.JSONDecodeError as ex:
                    # Arguments not complete yet, skip this tool call
                    logger.debug("JSON decode error for tool call %s: %s", i, ex)
                    continue
                except Exception as ex:  # pylint: disable=broad-except
                    logger.warning("Failed to create complete tool call: %s, error: %s", tool_call_data, ex)
                    continue

        return complete_tool_calls if complete_tool_calls else None

    def _create_streaming_tool_call_response(
        self,
        accumulated_tool_calls: list[dict],
        delta_arguments: Optional[dict[int, str]] = None,
        streaming_tool_names: Optional[set] = None,
    ) -> Optional[LlmResponse]:
        """Create a streaming tool call response with delta arguments.

        This method creates LlmResponse events for streaming tool call arguments,
        allowing real-time display of tool call arguments as they are generated.
        Only the delta (new content from this chunk) is included in the response.
        The agent layer is responsible for accumulating deltas.

        Args:
            accumulated_tool_calls: The accumulated tool calls so far (used to get name/id)
            delta_arguments: Dict mapping tool index to this chunk's delta arguments.
            streaming_tool_names: Set of tool names that should receive streaming events.
                                 If None, all tools receive streaming events.

        Returns:
            LlmResponse with delta tool call information, or None if no valid data
        """
        if not accumulated_tool_calls:
            return None

        parts = []
        for idx, tool_call_data in enumerate(accumulated_tool_calls):
            function_map: dict = tool_call_data.get(ToolKey.FUNCTION, {})
            name = function_map.get(ToolKey.NAME, "")
            tool_call_id = tool_call_data.get(ToolKey.ID, "")

            if not name:
                continue

            # Only process tools that are in the streaming_tool_names set
            if streaming_tool_names is not None and name not in streaming_tool_names:
                continue

            # Only process tool calls that have delta updates in this chunk
            if delta_arguments is None or idx not in delta_arguments:
                continue

            delta = delta_arguments[idx]
            # Delta mode: send only the delta for this chunk
            # Agent layer accumulates deltas to build complete JSON
            function_part = Part.from_function_call(name=name, args={const.TOOL_STREAMING_ARGS: delta})

            if tool_call_id:
                function_part.function_call.id = tool_call_id  # type: ignore

            parts.append(function_part)

        if not parts:
            return None

        streaming_content = Content(parts=parts, role=const.MODEL)
        return LlmResponse(
            content=streaming_content,
            partial=True,
        )

    def _verify_text_content_in_openai_message_response(
        self,
        response: dict,
        allow_content_none: bool = False,
    ) -> bool:
        """Verify if the text content exists in the openai message response.

        Args:
            response (`dict`):
                The JSON-format OpenAI response (After calling `model_dump`
                 function)
            allow_content_none (`bool`, defaults to `False`):
                If the content can be `None`

        Returns:
            `bool`: If the text content exists
        """
        choices: list[dict] = response.get(const.CHOICES, [{}])
        # Handle case where choices could be None (e.g., DeepSeek thinking events)
        if choices is None or not choices:
            return False
        if const.MESSAGE not in choices[0]:
            return False

        if not allow_content_none:
            return const.CONTENT in choices[0][const.MESSAGE]

        return True

    def _process_tool_calls_from_message(self, message: dict) -> Optional[List[ToolCall]]:
        """Process tool calls from a message.

        Args:
            message (`dict`): The message containing tool calls

        Returns:
            `Optional[List[ToolCall]]`: List of processed tool calls or None
        """
        tool_calls_data = message.get(const.TOOL_CALLS, [])
        if not tool_calls_data:
            return None

        tool_calls = []
        for tool_call in tool_calls_data:
            if tool_call is None:
                continue
            try:
                thought_sig = tool_call.get(ToolKey.THOUGHT_SIGNATURE)
                if not thought_sig and isinstance(tool_call.get(ToolKey.PROVIDER_SPECIFIC_FIELDS), dict):
                    thought_sig = tool_call[ToolKey.PROVIDER_SPECIFIC_FIELDS].get(ToolKey.THOUGHT_SIGNATURE)
                tool_calls.append(
                    ToolCall(
                        id=tool_call[ToolKey.ID],
                        name=tool_call[ToolKey.FUNCTION][ToolKey.NAME],
                        arguments=json.loads(tool_call[ToolKey.FUNCTION][ToolKey.ARGUMENTS]),
                        thought_signature=thought_sig,
                    ))
            except (KeyError, json.JSONDecodeError, TypeError) as ex:
                logger.warning("Failed to parse tool call: %s, error: %s", tool_call, ex)
                continue

        return tool_calls or None

    def _process_usage_from_response(self, response_dict: dict) -> Optional[GenerateContentResponseUsageMetadata]:
        """Process usage information from a response.

        Args:
            response_dict (`dict`): The response dictionary containing usage information

        Returns:
            `Optional[GenerateContentResponseUsageMetadata]`: Processed usage metadata or None
        """
        if const.USAGE not in response_dict:
            return None

        usage_data: dict[str, int] = response_dict[const.USAGE]
        return GenerateContentResponseUsageMetadata(
            prompt_token_count=usage_data.get("prompt_tokens", 0),
            candidates_token_count=usage_data.get("completion_tokens", 0),
            total_token_count=usage_data.get("total_tokens", 0),
        )

    def _create_response_without_content(self, response_dict: dict) -> LlmResponse:
        """Create a LlmResponse without content."""
        # Parse usage if available
        usage = self._process_usage_from_response(response_dict)

        # Get finish reason
        choices: list[dict] = response_dict.get(const.CHOICES, [{}])
        error_code = None
        if choices and choices[0].get(const.FINISH_REASON):
            finish_reason_value = choices[0][const.FINISH_REASON]
            if finish_reason_value != FinishReason.STOP.value:
                error_code = finish_reason_value

        # Get response ID
        response_id = response_dict.get("id")

        return LlmResponse(content=None, usage_metadata=usage, error_code=error_code, response_id=response_id)

    def _create_response_with_content(self, response_dict: dict) -> LlmResponse:
        """Create a LlmResponse with text content."""
        choices: list[dict] = response_dict.get(const.CHOICES, [{}])
        choice = choices[0] if choices else {}
        message: dict = choice.get(const.MESSAGE, {})

        # Extract content
        text_content = message.get(const.CONTENT, "")

        # Check for tool calls
        tool_calls = self._process_tool_calls_from_message(message)

        # If add_tools_to_prompt is enabled and we have text content, try to parse function calls from it
        if self.add_tools_to_prompt and text_content and not tool_calls:
            try:
                tool_prompt = self._create_tool_prompt()
                parsed_function_calls = tool_prompt.parse_function(text_content)
                if parsed_function_calls:
                    # Convert FunctionCall objects to ToolCall objects
                    tool_calls = []
                    for func_call in parsed_function_calls:
                        tool_call = ToolCall(
                            id=f"call_{uuid.uuid4().hex[:24]}",
                            name=func_call.name,  # type: ignore
                            arguments=func_call.args)  # type: ignore
                        tool_calls.append(tool_call)
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Failed to parse function calls from text content: %s", ex)

        parts = []

        # Add text content if present
        if text_content:
            content_part = Part.from_text(text=text_content)
            content_part.thought = False  # Regular text content is not thought
            parts.append(content_part)

        # Add function calls if present
        if tool_calls:
            for tool_call in tool_calls:
                # Create Part with function_call using the from_function_call method
                function_part = Part.from_function_call(name=tool_call.name, args=tool_call.arguments)
                # Set the id if available
                if tool_call.id:
                    function_part.function_call.id = tool_call.id  # type: ignore
                self._set_part_thought_signature(function_part, tool_call.thought_signature)
                parts.append(function_part)

        # Create content with parts
        if parts:
            content = Content(parts=parts, role=const.MODEL)
        else:
            # Fallback to empty text content if no parts
            empty_part = Part.from_text(text="")
            empty_part.thought = False  # Empty content is not thought
            content = Content(parts=[empty_part], role=const.MODEL)

        # Parse usage if available
        usage = self._process_usage_from_response(response_dict)

        # Get finish reason for error handling
        error_code = None
        if choice.get(const.FINISH_REASON) and choice[const.FINISH_REASON] != FinishReason.STOP.value:
            error_code = choice[const.FINISH_REASON]

        # Get response ID
        response_id = response_dict.get("id")

        return LlmResponse(content=content, usage_metadata=usage, error_code=error_code, response_id=response_id)

    async def _generate_single(self,
                               api_params: Dict,
                               request: LlmRequest,
                               http_options: Dict[str, Any] | None = None,
                               ctx: InvocationContext | None = None) -> LlmResponse:
        """Generate a single response (non-streaming)."""
        if http_options is None:
            http_options = {}
        client = self._create_async_client()
        try:
            response = await client.chat.completions.create(**api_params, **http_options)
            response_dict: dict = response.model_dump()

            # Check if the response contains valid text content or tool calls
            has_text_content = self._verify_text_content_in_openai_message_response(response_dict)
            has_tool_calls = False

            # Check for tool calls
            choices: list[dict] = response_dict.get(const.CHOICES, [{}])
            if choices and choices[0].get(const.MESSAGE, {}).get(const.TOOL_CALLS):
                has_tool_calls = True

            # Create response with content if we have text or tool calls
            if has_text_content or has_tool_calls:
                return self._create_response_with_content(response_dict)
            else:
                return self._create_response_without_content(response_dict)
        finally:
            await client.close()

    def _convert_tools_to_openai_format(self, tools: List[Tool]) -> List[Dict[str, Any]]:
        """Convert Google GenAI tools format to OpenAI tools format.

        Args:
            tools: List of Google GenAI Tool objects

        Returns:
            List of OpenAI-formatted tool dictionaries
        """
        openai_tools = []

        for tool in tools:
            # Handle Google GenAI Tool objects with function_declarations
            if tool.function_declarations:
                for func_decl in tool.function_declarations:
                    openai_tool = {
                        "type": "function",
                        "function": {
                            "name": func_decl.name or "",
                            "description": func_decl.description or "",
                        },
                    }

                    # Convert parameters schema - always include parameters field
                    if func_decl.parameters:
                        openai_tool["function"]["parameters"] = self._convert_schema_to_openai_format(
                            func_decl.parameters)
                    else:
                        # When parameters are empty, provide the proper OpenAI format structure
                        openai_tool["function"]["parameters"] = {"type": "object", "properties": {}}

                    openai_tools.append(openai_tool)

            # Handle already converted/direct OpenAI format
            elif isinstance(tool, dict) and tool.get("type") == "function":
                openai_tools.append(tool)

        return openai_tools

    def _clip_tool_response_text(self, text: str, tool_name: str) -> str:
        """Hard-clip tool response text to protect model context budget."""
        limit = self._tool_response_clip_chars
        if limit <= 0 or len(text) <= limit:
            return text
        truncated = len(text) - limit
        suffix = f"\n...[TRUNCATED {truncated} CHARS FROM TOOL RESPONSE: {tool_name}]"
        keep = max(0, limit - len(suffix))
        return text[:keep] + suffix

    def _convert_schema_to_openai_format(self, schema: Schema) -> Dict[str, Any]:
        """Convert Google GenAI Schema to OpenAI parameters format.

        Args:
            schema: Google GenAI Schema object

        Returns:
            OpenAI-formatted parameters dictionary
        """
        if not schema:
            # Return proper OpenAI format structure for empty schema
            return {"type": "object", "properties": {}}

        result = {}

        # Handle type
        if schema.type:
            # Convert Google GenAI Type enum to string
            if hasattr(schema.type, "value"):
                result["type"] = schema.type.value.lower()
            else:
                result["type"] = str(schema.type).lower()
        else:
            # Default to object type if not specified
            result["type"] = "object"

        # Handle properties
        if schema.properties:
            result["properties"] = {}
            for prop_name, prop_schema in schema.properties.items():
                result["properties"][prop_name] = self._convert_schema_to_openai_format(prop_schema)
        else:
            # Ensure properties field exists for object type
            if result.get("type") == "object":
                result["properties"] = {}

        # Handle description
        if schema.description:
            result["description"] = schema.description

        # Handle required fields
        if schema.required:
            result["required"] = schema.required

        # Handle items for arrays
        if schema.items:
            result["items"] = self._convert_schema_to_openai_format(schema.items)

        # Handle additional properties
        if schema.additional_properties is not None:
            result["additionalProperties"] = schema.additional_properties

        return result

    def _ensure_additional_properties_false(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively ensure all object types in JSON Schema have additionalProperties: false.

        OpenAI API requires all object types to explicitly set additionalProperties: false.
        Note: Only GPT models need this parameter. Adding this parameter to other models may cause API calls to fail.

        Args:
            schema: JSON Schema dictionary

        Returns:
            Processed JSON Schema dictionary
        """
        # Only GPT models need additionalProperties: false, other models return the original schema
        if "gpt" not in self._model_name.lower():
            return schema

        if not isinstance(schema, dict):
            return schema

        result = schema.copy()

        # Check if it's an object type: type: object or has properties field
        if result.get("type") == "object" or "properties" in result:
            result["additionalProperties"] = False
            result.setdefault("type", "object")
            result.setdefault("properties", {})

        # Process all fields that may contain nested schemas
        for key, value in result.items():
            if isinstance(value, dict):
                result[key] = self._ensure_additional_properties_false(value)
            elif isinstance(value, list):
                result[key] = [
                    self._ensure_additional_properties_false(item) if isinstance(item, dict) else item for item in value
                ]

        return result

    def _build_response_format(self, config: GenerateContentConfig) -> Optional[Dict[str, Any]]:
        """Build OpenAI response format from Google GenAI config.

        Args:
            config: Google GenAI GenerateContentConfig object

        Returns:
            OpenAI response format dictionary or None
        """
        # Handle response_mime_type and response_schema
        if config.response_mime_type == "application/json":
            if config.response_schema:
                # response_schema must be pydantic.BaseModel
                if not isinstance(config.response_schema, type(BaseModel)):
                    raise ValueError(f"{type(config.response_schema)} must be pydantic.BaseModel")
                openai_schema = config.response_schema.model_json_schema()  # type: ignore
                openai_schema = self._ensure_additional_properties_false(openai_schema)
                return {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "response_schema",
                        "schema": openai_schema,
                        "strict": True
                    },
                }
            elif config.response_json_schema:
                # Use provided JSON schema directly
                processed_schema = self._ensure_additional_properties_false(config.response_json_schema)
                return {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "response_schema",
                        "schema": processed_schema,
                        "strict": True
                    },
                }
            else:
                # Basic JSON mode
                return {"type": "json_object"}

        return None

    def _extract_http_options(self, config: GenerateContentConfig) -> Dict[str, Any]:
        """Extract HTTP options from config for OpenAI API calls.

        Args:
            config: Google GenAI GenerateContentConfig object

        Returns:
            Dictionary containing OpenAI-compatible HTTP options
        """
        http_opts = {}

        if not config.http_options:
            return http_opts

        http_options = config.http_options

        if http_options.headers:
            # Process headers, invoking callables if present
            processed_headers = {}
            for key, value in http_options.headers.items():
                if callable(value):
                    processed_headers[key] = value()
                else:
                    processed_headers[key] = value
            http_opts["extra_headers"] = processed_headers

        if http_options.timeout is not None:
            http_opts["timeout"] = http_options.timeout / 1000.0

        if http_options.extra_body:
            # Process extra_body, invoking callables if present
            processed_extra_body = {}
            for key, value in http_options.extra_body.items():
                if callable(value):
                    processed_extra_body[key] = value()
                else:
                    processed_extra_body[key] = value
            http_opts["extra_body"] = processed_extra_body

        return http_opts

    def _merge_configs(self, request_config: Optional[GenerateContentConfig]) -> GenerateContentConfig:
        """Merge the default generate_content_config with the request config.

        The request config takes precedence over the default config.
        If a field is set in the request config, it will override the default.
        If a field is not set in the request config, the default value will be used.

        Args:
            request_config: Config from the request (can be None)

        Returns:
            Merged GenerateContentConfig
        """
        # If no request config provided, use default config if available
        if not request_config:
            if self.generate_content_config:
                return self.generate_content_config.model_copy(deep=True)
            return GenerateContentConfig()

        # If no default config, return request config as is
        if not self.generate_content_config:
            return request_config

        # Get explicitly set fields from request_config using model_fields_set
        # This is more reliable than model_dump(exclude_unset=True) for enum handling
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
        """Log warnings for configuration options that are not supported in OpenAI.

        Args:
            config: Google GenAI GenerateContentConfig object
        """
        unsupported_options = []

        if config.top_k is not None:
            unsupported_options.append("top_k")
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
                "The following configuration options are not supported in OpenAI models and will be ignored: %s",
                ', '.join(unsupported_options))

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

        # Prepare OpenAI API parameters
        messages = self._format_messages(request)

        # Debug log the formatted messages to help with troubleshooting
        logger.debug("Formatted messages for OpenAI API: %s", json.dumps(messages, indent=2))

        api_params = {
            ApiParamsKey.MODEL: self._model_name,
            ApiParamsKey.MESSAGES: messages,
            ApiParamsKey.STREAM: stream,
        }

        # Add configuration parameters
        try:
            if request.config:
                # Log warnings for unsupported configuration options
                self._log_unsupported_config_options(request.config)
                if request.config.max_output_tokens:
                    # Use max_completion_tokens for newer models (preferred), fallback to max_tokens
                    api_params[ApiParamsKey.MAX_COMPLETION_TOKENS] = request.config.max_output_tokens
                    # Keep max_tokens for backward compatibility (skip for gpt models)
                    if "gpt-5" not in self._model_name.lower():
                        api_params[ApiParamsKey.MAX_TOKENS] = request.config.max_output_tokens
                if request.config.temperature is not None:
                    api_params[ApiParamsKey.TEMPERATURE] = request.config.temperature
                if request.config.top_p is not None:
                    api_params[ApiParamsKey.TOP_P] = request.config.top_p
                if request.config.stop_sequences:
                    api_params[ApiParamsKey.STOP] = request.config.stop_sequences

                # Additional OpenAI-specific parameters
                if request.config.frequency_penalty is not None:
                    api_params[ApiParamsKey.FREQUENCY_PENALTY] = request.config.frequency_penalty
                if request.config.presence_penalty is not None:
                    api_params[ApiParamsKey.PRESENCE_PENALTY] = request.config.presence_penalty
                if request.config.seed is not None:
                    api_params[ApiParamsKey.SEED] = request.config.seed

                # Handle candidate count (maps to OpenAI's 'n' parameter)
                if request.config.candidate_count is not None and request.config.candidate_count > 0:
                    api_params[ApiParamsKey.N] = request.config.candidate_count

                # Handle logprobs configuration
                if request.config.response_logprobs is not None:
                    api_params[ApiParamsKey.LOGPROBS] = request.config.response_logprobs
                if request.config.logprobs is not None and request.config.logprobs > 0:
                    api_params[ApiParamsKey.TOP_LOGPROBS] = request.config.logprobs

                # Currently, not support response_format for OpenAI
                # Handle response format for structured output
                response_format = self._build_response_format(request.config)
                if response_format:
                    api_params[ApiParamsKey.RESPONSE_FORMAT] = response_format

                # Handle tools - convert from Google GenAI format to OpenAI format
                if not self.add_tools_to_prompt and request.config.tools:
                    converted_tools = self._convert_tools_to_openai_format(request.config.tools)  # type: ignore
                    if converted_tools:
                        api_params[ApiParamsKey.TOOLS] = converted_tools
                        api_params[ApiParamsKey.TOOL_CHOICE] = "auto"
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error in OpenAI API parameters: %s", ex, exc_info=True)
            raise ex

        # Extract HTTP options for API calls
        http_options = {}
        if request.config:
            http_options = self._extract_http_options(request.config)
        # set thinking params
        self._set_thinking(request, http_options)

        try:
            if stream:
                async for response in self._generate_stream(api_params, request, http_options, ctx):
                    yield response
            else:
                response = await self._generate_single(api_params, request, http_options, ctx)
                yield response
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("OpenAI API error: %s", ex)
            # Create error response using LlmResponse fields
            yield LlmResponse(content=None,
                              error_code="API_ERROR",
                              error_message=str(ex),
                              custom_metadata={"error": str(ex)})

    async def _generate_stream(self,
                               api_params: Dict,
                               request: LlmRequest,
                               http_options: Dict[str, Any] | None = None,
                               ctx: InvocationContext | None = None) -> AsyncGenerator[LlmResponse, None]:
        """Generate streaming responses."""
        if http_options is None:
            http_options = {}
        thought_content = ""
        accumulated_content = ""
        last_usage = None
        accumulated_tool_calls: list[dict] = []
        is_thinking = False  # Track whether we're currently in thinking mode
        response_id: str | None = None  # Track response ID from API

        # For streaming tool call arguments - get the set of tool names that should stream
        streaming_tool_names = getattr(request, 'streaming_tool_names', None) or set()

        # Create tool prompt instance for streaming if needed
        tool_prompt = None
        if self.add_tools_to_prompt:
            tool_prompt = self._create_tool_prompt()

        client = self._create_async_client()
        try:
            logger.debug("openai invoke with params: %s", api_params)
            response = await client.chat.completions.create(**api_params, **http_options)
            if response is None:
                raise ValueError("Empty response from API")

            async for chunk in response:
                if chunk is None:
                    continue

                chunk_dict: dict = chunk.model_dump()
                logger.debug("🔥 RAW LLM CHUNK: %s", json.dumps(chunk_dict, ensure_ascii=False))

                # Capture response ID from chunk (only set once from first chunk that has it)
                if response_id is None and chunk_dict.get("id"):
                    response_id = chunk_dict.get("id")

                # Check for thinking events first
                if self._is_thinking_event(chunk_dict):
                    thinking_state = self._get_thinking_state(chunk_dict)
                    if thinking_state == 0:
                        # Start thinking
                        is_thinking = True
                    elif thinking_state == 2:
                        # End thinking
                        is_thinking = False
                    # Handle thinking events - these are metadata about thinking state
                    # We can log them but don't need to yield them as content
                    continue

                # Verify if the chunk contains valid text content
                if not self._verify_text_content_in_delta_response(chunk_dict):
                    # Process chunk without content (this is where tool calls are streamed)
                    _, usage, delta_arguments = self._process_chunk_without_content(chunk_dict, accumulated_tool_calls)

                    if usage:
                        last_usage = usage

                    # If streaming tool call arguments is enabled, yield partial tool call events
                    # delta_arguments being non-empty means tool calls were processed
                    if streaming_tool_names and delta_arguments and accumulated_tool_calls:
                        # Yield streaming tool call event with delta arguments
                        # Only for tools in streaming_tool_names
                        streaming_event = self._create_streaming_tool_call_response(accumulated_tool_calls,
                                                                                    delta_arguments,
                                                                                    streaming_tool_names)
                        if streaming_event:
                            yield streaming_event

                    continue

                # Process chunk with valid content
                choices = chunk_dict.get(const.CHOICES, [])
                if not choices:
                    continue  # Skip if no choices available
                choice: dict[str, dict] = choices[0]
                delta = choice[const.DELTA]

                # Handle reasoning content (thinking content) first
                if delta.get(const.REASONING_CONTENT):
                    reasoning_content = delta.get(const.REASONING_CONTENT)
                    if reasoning_content is not None:
                        # Reasoning content is always thinking content
                        thought_content += reasoning_content

                        # Set thought flag to True for reasoning content
                        content_part = Part.from_text(text=reasoning_content)
                        content_part.thought = True

                        partial_content = Content(parts=[content_part], role=const.MODEL)
                        yield LlmResponse(content=partial_content,
                                          partial=True,
                                          response_id=response_id,
                                          custom_metadata={const.CHUNK: chunk_dict})

                # Handle regular content
                if delta.get(const.CONTENT):
                    content = delta.get(const.CONTENT)
                    if content is not None:
                        if not is_thinking:
                            accumulated_content += content
                        else:
                            thought_content += content

                        # Set thought flag based on current thinking state
                        content_part = Part.from_text(text=content)
                        content_part.thought = is_thinking

                        partial_content = Content(parts=[content_part], role=const.MODEL)
                        yield LlmResponse(content=partial_content,
                                          partial=True,
                                          response_id=response_id,
                                          custom_metadata={const.CHUNK: chunk_dict})

                # Handle usage
                usage = self._process_usage(chunk_dict)
                if usage:
                    last_usage = usage

            # Yield final complete response
            final_content = None

            parts = []

            if thought_content:
                logger.debug("Final accumulated thought content: %s...", thought_content[:200])
                content_part = Part.from_text(text=thought_content)
                content_part.thought = True
                parts.append(content_part)

            # Parse function calls from final accumulated content if add_tools_to_prompt is enabled
            complete_tool_calls = self._create_complete_tool_calls(accumulated_tool_calls)
            if tool_prompt and accumulated_content and not complete_tool_calls:
                try:
                    parsed_function_calls = tool_prompt.parse_function(accumulated_content)
                    if parsed_function_calls:
                        # Convert FunctionCall objects to ToolCall objects
                        complete_tool_calls = []
                        for func_call in parsed_function_calls:
                            tool_call = ToolCall(id=f"call_{uuid.uuid4().hex[:24]}",
                                                 name=func_call.name,
                                                 arguments=func_call.args)
                        complete_tool_calls.append(tool_call)
                        logger.debug("Parsed %s function calls from final accumulated content",
                                     len(complete_tool_calls))
                except Exception as ex:  # pylint: disable=broad-except
                    logger.warning("Failed to parse function calls from final accumulated content: %s", ex)

            # Add text content if present
            if accumulated_content:
                logger.debug("Final accumulated regular content: %s...", accumulated_content[:200])
                content_part = Part.from_text(text=accumulated_content)
                content_part.thought = False  # Final accumulated content represents the answer, not thinking
                parts.append(content_part)

            if complete_tool_calls:
                for tool_call in complete_tool_calls:
                    # Create Part with function_call using the from_function_call method
                    function_part = Part.from_function_call(name=tool_call.name, args=tool_call.arguments)
                    # Set the id if available
                    if tool_call.id:
                        function_part.function_call.id = tool_call.id  # type: ignore
                    self._set_part_thought_signature(function_part, tool_call.thought_signature)
                    parts.append(function_part)

            # Create final content with parts
            if parts:
                final_content = Content(parts=parts, role=const.MODEL)

            # Convert usage to the expected format for LlmResponse
            final_usage = None
            if last_usage:
                # Create a compatible usage metadata object
                final_usage = last_usage  # Use the existing usage object for now

            yield LlmResponse(
                content=final_content,
                usage_metadata=final_usage,
                partial=False,
                response_id=response_id,
                custom_metadata={"stream_complete": True},
            )

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error in streaming response: %s", ex, exc_info=True)
            # Create error response using LlmResponse fields
            yield LlmResponse(
                content=None,
                error_code="STREAMING_ERROR",
                error_message=f"Error in streaming: {str(ex)}",
                partial=False,
                custom_metadata={"error": str(ex)},
            )
        finally:
            await client.close()
