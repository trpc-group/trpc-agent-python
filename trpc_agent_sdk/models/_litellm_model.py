# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""LiteLLMModel: LLM via LiteLLM (provider/model, e.g. openai/gpt-4o).
Inherits OpenAIModel, overrides API to litellm.acompletion."""

import importlib.util
import json
import os
from enum import Enum
from typing import Any
from typing import AsyncGenerator
from typing import Dict
from typing import List
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import GenerateContentResponseUsageMetadata
from trpc_agent_sdk.types import Part

from . import _constants as const
from ._llm_request import LlmRequest
from ._llm_response import LlmResponse
from ._openai_model import FinishReason
from ._openai_model import OpenAIModel
from ._registry import register_model

_LITELLM_SUPPORTED_MODELS: List[str] = [
    r"openai/.*",
    r"anthropic/.*",
    r"groq/.*",
    r"azure/.*",
    r"gemini/.*",
    r"vertex_ai/.*",
    r"ollama/.*",
    r"ollama_chat/.*",
    r"together_ai/.*",
    r"cohere/.*",
    r"mistral/.*",
    r"deepseek/.*",
]


def _is_litellm_gemini_model(model_string: str) -> bool:
    """True for gemini/gemini-* or vertex_ai/gemini-*."""
    return model_string.startswith(("gemini/gemini-", "vertex_ai/gemini-"))


def _build_response_format_for_litellm(
    response_schema: Any,
    model_name: str,
) -> Optional[Dict[str, Any]]:
    """Build response_format: Gemini → response_schema; OpenAI-compatible → json_schema."""
    schema_dict: Dict[str, Any]
    schema_name: str = "response_schema"
    if isinstance(response_schema, type) and hasattr(response_schema, "model_json_schema"):
        schema_dict = response_schema.model_json_schema()  # type: ignore[union-attr]
        schema_name = getattr(response_schema, "__name__", schema_name) or schema_name
    elif hasattr(response_schema, "model_dump"):
        schema_dict = response_schema.model_dump()
        schema_name = getattr(response_schema, "__name__", None) or getattr(getattr(
            response_schema, "__class__", None), "__name__", None) or schema_dict.get("title", schema_name)
        if not isinstance(schema_name, str):
            schema_name = "response_schema"
    elif isinstance(response_schema, dict):
        schema_dict = dict(response_schema)
        schema_name = str(schema_dict.get("title", "response_schema"))
    else:
        return None

    if _is_litellm_gemini_model(model_name):
        return {
            "type": "json_object",
            "response_schema": schema_dict,
        }

    if (isinstance(schema_dict, dict) and schema_dict.get("type") == "object"
            and "additionalProperties" not in schema_dict):
        schema_dict = dict(schema_dict)
        schema_dict["additionalProperties"] = False

    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name,
            "strict": True,
            "schema": schema_dict,
        },
    }


class _LiteLLMApiParamsKey(str, Enum):
    MODEL = const.MODEL
    MESSAGES = "messages"
    STREAM = "stream"
    TOOLS = "tools"
    TOOL_CHOICE = "tool_choice"
    RESPONSE_FORMAT = "response_format"
    MAX_COMPLETION_TOKENS = "max_completion_tokens"
    TEMPERATURE = "temperature"
    TOP_P = "top_p"
    STOP = "stop"
    API_KEY = "api_key"
    API_BASE = "api_base"
    STREAM_OPTS = "stream_options"


@register_model(model_name="LiteLLMModel", supported_models=_LITELLM_SUPPORTED_MODELS)
class LiteLLMModel(OpenAIModel):
    """model_name must be provider/model (e.g. openai/gpt-4o). kwargs: api_key, base_url→api_base."""

    _litellm_imported: bool = False

    def __init__(
        self,
        model_name: str,
        filters_name: Optional[list[str]] = None,
        generate_content_config: Optional[GenerateContentConfig] = None,
        **kwargs,
    ):
        if "/" not in model_name:
            raise ValueError(
                "model_name must be in provider/model format (e.g. openai/gpt-4o, anthropic/claude-3-5-sonnet)")
        super().__init__(
            model_name,
            filters_name,
            add_tools_to_prompt=False,
            tool_prompt="xml",
            generate_content_config=generate_content_config,
            **kwargs,
        )

    def _ensure_litellm_imported(self) -> None:
        """Lazy-import litellm; set LITELLM_MODE=PRODUCTION. Raises ImportError if not installed."""
        if LiteLLMModel._litellm_imported:
            return
        if importlib.util.find_spec("litellm") is None:
            raise ImportError(
                "LiteLLM support requires: pip install trpc-agent-py[litellm] or pip install litellm>=1.75.5")
        os.environ.setdefault("LITELLM_MODE", "PRODUCTION")
        LiteLLMModel._litellm_imported = True

    def _get_message_content(self, message: Any) -> str:
        """Extract text from message.content (str or list of blocks). message: dict."""
        content = message.get("content") if message else None
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts: List[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and "text" in block:
                    texts.append(block["text"])
                elif "text" in block:
                    texts.append(block["text"])
            return " ".join(texts) if texts else ""
        return str(content)

    def _create_response_with_content(self, response_dict: Dict[str, Any], partial: bool = False) -> LlmResponse:
        """Build LlmResponse from choices[0].message + usage."""
        choices = response_dict.get(const.CHOICES) or []
        if not choices:
            usage_meta = super()._process_usage_from_response(response_dict)
            return LlmResponse(
                content=None,
                usage_metadata=usage_meta,
                error_code="NO_CHOICES",
                error_message="No choices in response",
            )

        first_choice = choices[0] or {}
        message = first_choice.get(const.MESSAGE)
        if not message:
            usage_meta = super()._process_usage_from_response(response_dict)
            return LlmResponse(
                content=None,
                usage_metadata=usage_meta,
                error_code="NO_MESSAGE",
                error_message="No message in choice",
            )

        parts: List[Part] = []
        text = self._get_message_content(message)
        if text:
            content_part = Part.from_text(text=text)
            content_part.thought = False
            parts.append(content_part)

        message_dict = message if isinstance(message, dict) else (getattr(message, "model_dump", lambda: {})() or {})
        tool_calls = super()._process_tool_calls_from_message(message_dict)
        if tool_calls:
            for tool_call in tool_calls:
                part = Part.from_function_call(name=tool_call.name, args=tool_call.arguments)
                if tool_call.id and hasattr(part.function_call, "id"):
                    part.function_call.id = tool_call.id  # type: ignore
                parts.append(part)

        if not parts:
            empty_part = Part.from_text(text="")
            empty_part.thought = False
            parts.append(empty_part)

        content = Content(parts=parts, role=const.MODEL)
        usage_meta = super()._process_usage_from_response(response_dict)
        error_code = None
        finish_reason = first_choice.get(const.FINISH_REASON)
        if finish_reason and finish_reason != FinishReason.STOP.value:
            error_code = finish_reason
        return LlmResponse(content=content, usage_metadata=usage_meta, partial=partial, error_code=error_code)

    @override
    def _log_unsupported_config_options(self, config: GenerateContentConfig) -> None:
        """Log unsupported config options (ignored by LiteLLM)."""
        unsupported_options = []

        if config.top_k is not None:
            unsupported_options.append("top_k")
        if config.response_logprobs is not None:
            unsupported_options.append("response_logprobs")
        if config.logprobs is not None and config.logprobs > 0:
            unsupported_options.append("logprobs")
        if config.candidate_count is not None and config.candidate_count > 1:
            unsupported_options.append("candidate_count > 1")
        if config.safety_settings:
            unsupported_options.append("safety_settings")
        if config.cached_content:
            unsupported_options.append("cached_content")
        if getattr(config, "response_modalities", None):
            unsupported_options.append("response_modalities")
        if getattr(config, "media_resolution", None):
            unsupported_options.append("media_resolution")
        if getattr(config, "speech_config", None):
            unsupported_options.append("speech_config")
        if getattr(config, "audio_timestamp", None):
            unsupported_options.append("audio_timestamp")
        if getattr(config, "automatic_function_calling", None):
            unsupported_options.append("automatic_function_calling")

        if unsupported_options:
            logger.warning(
                "The following configuration options are not supported in LiteLLM models and will be ignored: "
                f"{', '.join(unsupported_options)}", )

    @override
    async def _generate_async_impl(
        self,
        request: LlmRequest,
        stream: bool = False,
        ctx: InvocationContext | None = None,
    ) -> AsyncGenerator[LlmResponse, None]:
        """Generate via litellm.acompletion()."""
        self._ensure_litellm_imported()
        self.validate_request(request)
        merged_config = self._merge_configs(request.config)
        request.config = merged_config
        messages = self._format_messages(request)
        logger.debug("Formatted messages for LiteLLM API: %s", json.dumps(messages, indent=2))

        try:
            if request.config:
                self._log_unsupported_config_options(request.config)
            api_params: Dict[str, Any] = {
                _LiteLLMApiParamsKey.MODEL: self._model_name,
                _LiteLLMApiParamsKey.MESSAGES: messages,
                _LiteLLMApiParamsKey.STREAM: stream,
            }
            if request.config and request.config.tools:
                converted_tools = self._convert_tools_to_openai_format(request.config.tools)
                if converted_tools:
                    api_params[_LiteLLMApiParamsKey.TOOLS] = converted_tools
                    if messages and messages[-1].get(const.ROLE) == const.TOOL:
                        api_params[_LiteLLMApiParamsKey.TOOL_CHOICE] = "none"
                    else:
                        api_params[_LiteLLMApiParamsKey.TOOL_CHOICE] = "auto"
            if request.config and getattr(request.config, "response_schema", None):
                rf = _build_response_format_for_litellm(
                    request.config.response_schema,
                    self._model_name,
                )
                if rf is not None:
                    api_params[_LiteLLMApiParamsKey.RESPONSE_FORMAT] = rf
            if request.config:
                if request.config.max_output_tokens is not None:
                    api_params[_LiteLLMApiParamsKey.MAX_COMPLETION_TOKENS] = request.config.max_output_tokens
                if request.config.temperature is not None:
                    api_params[_LiteLLMApiParamsKey.TEMPERATURE] = request.config.temperature
                if request.config.top_p is not None:
                    api_params[_LiteLLMApiParamsKey.TOP_P] = request.config.top_p
                if request.config.stop_sequences:
                    api_params[_LiteLLMApiParamsKey.STOP] = request.config.stop_sequences
            if self._api_key:
                api_params[_LiteLLMApiParamsKey.API_KEY] = self._api_key
            if self._base_url:
                api_params[_LiteLLMApiParamsKey.API_BASE] = self._base_url
            api_params.update(self.config)
            if stream:
                api_params[_LiteLLMApiParamsKey.STREAM_OPTS] = {"include_usage": True}

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error in LiteLLM API parameters: %s", ex, exc_info=True)
            raise

        try:
            if stream:
                async for response in self._generate_stream(api_params, request, ctx):
                    yield response
            else:
                response = await self._generate_single(api_params, request, ctx)
                yield response
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("LiteLLM API error: %s", ex)
            yield LlmResponse(
                content=None,
                error_code="API_ERROR",
                error_message=str(ex),
                custom_metadata={"error": str(ex)},
            )

    async def _generate_single(
        self,
        api_params: Dict[str, Any],
        request: LlmRequest,
        ctx: InvocationContext | None = None,
    ) -> LlmResponse:
        """One-shot acompletion → LlmResponse."""
        try:
            litellm = __import__("litellm")
            acompletion = getattr(litellm, "acompletion")
            response = await acompletion(**api_params)
            response_dict: Dict[str, Any] = (response.model_dump() if hasattr(response, "model_dump") else response)
            return self._create_response_with_content(response_dict, partial=False)
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("LiteLLM API error: %s", ex)
            return LlmResponse(
                content=None,
                error_code="API_ERROR",
                error_message=str(ex),
                custom_metadata={"error": str(ex)},
            )

    async def _generate_stream(
        self,
        api_params: Dict[str, Any],
        request: LlmRequest,
        ctx: InvocationContext | None = None,
    ) -> AsyncGenerator[LlmResponse, None]:
        """Stream via acompletion(stream=True); yield deltas then final LlmResponse."""
        try:
            litellm = __import__("litellm")
            acompletion = getattr(litellm, "acompletion")
            stream_handle = await acompletion(**api_params)

            accumulated_content = ""
            accumulated_tool_calls: List[Dict[str, Any]] = []
            usage_meta: Optional[GenerateContentResponseUsageMetadata] = None

            async for chunk in stream_handle:
                if chunk is None:
                    continue
                chunk_dict: Dict[str, Any] = (chunk.model_dump() if hasattr(chunk, "model_dump") else chunk)
                choices = chunk_dict.get(const.CHOICES) or []
                if choices:
                    choice = choices[0] or {}
                    delta = (choice.get(const.DELTA)
                             if isinstance(choice, dict) else getattr(choice, "delta", None)) or {}
                    content_delta = (delta.get(const.CONTENT) if isinstance(delta, dict) else getattr(
                        delta, "content", None))
                    tool_calls_data = delta.get(const.TOOL_CALLS) if isinstance(delta, dict) else None
                    if tool_calls_data:
                        for tc_delta in tool_calls_data or []:
                            if tc_delta is None:
                                continue
                            try:
                                self._process_tool_call_delta(tc_delta, accumulated_tool_calls)
                            except Exception as ex:  # pylint: disable=broad-except
                                logger.error("Error processing tool call delta: %s", ex)
                    if content_delta:
                        accumulated_content += content_delta
                        content_part = Part.from_text(text=content_delta)
                        content_part.thought = False
                        yield LlmResponse(
                            content=Content(parts=[content_part], role=const.MODEL),
                            partial=True,
                            custom_metadata={const.CHUNK: chunk_dict},
                        )
                usage = super()._process_usage(chunk_dict)
                if usage:
                    usage_meta = usage
            parts: List[Part] = []
            if accumulated_content:
                content_part = Part.from_text(text=accumulated_content)
                content_part.thought = False
                parts.append(content_part)
            complete_tool_calls = self._create_complete_tool_calls(accumulated_tool_calls) or []
            for tool_call in complete_tool_calls:
                part = Part.from_function_call(name=tool_call.name, args=tool_call.arguments)
                if tool_call.id and hasattr(part.function_call, "id"):
                    part.function_call.id = tool_call.id  # type: ignore
                parts.append(part)
            final_content = Content(parts=parts, role=const.MODEL) if parts else None
            yield LlmResponse(
                content=final_content,
                usage_metadata=usage_meta,
                partial=False,
                custom_metadata={"stream_complete": True},
            )
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error in streaming response: %s", ex, exc_info=True)
            yield LlmResponse(
                content=None,
                error_code="STREAMING_ERROR",
                error_message=str(ex),
                custom_metadata={"error": str(ex)},
            )
