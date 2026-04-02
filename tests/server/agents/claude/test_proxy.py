# -*- coding: utf-8 -*-
"""Unit tests for AnthropicProxyApp and related Pydantic models."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse
from trpc_agent_sdk.types import Content, FunctionCall, FunctionResponse, GenerateContentConfig, Part, Schema, Tool
from trpc_agent_sdk.server.agents.claude._proxy import (
    AddModelRequest,
    AddModelResponse,
    AnthropicMessage,
    AnthropicMessagesRequest,
    AnthropicMessagesResponse,
    AnthropicProxyApp,
    AnthropicTool,
    ContentBlockImage,
    ContentBlockText,
    ContentBlockToolResult,
    ContentBlockToolUse,
    DeleteModelRequest,
    DeleteModelResponse,
    SystemContent,
    TokenCountRequest,
    TokenCountResponse,
    Usage,
)


# ---------------------------------------------------------------------------
# Pydantic Model Tests
# ---------------------------------------------------------------------------

class TestPydanticModels:
    def test_content_block_text(self):
        block = ContentBlockText(type="text", text="hello")
        assert block.type == "text"
        assert block.text == "hello"

    def test_content_block_image(self):
        block = ContentBlockImage(type="image", source={"type": "base64", "data": "abc"})
        assert block.type == "image"

    def test_content_block_tool_use(self):
        block = ContentBlockToolUse(type="tool_use", id="t1", name="search", input={"q": "test"})
        assert block.id == "t1"
        assert block.name == "search"

    def test_content_block_tool_result(self):
        block = ContentBlockToolResult(type="tool_result", tool_use_id="t1", content="result text")
        assert block.tool_use_id == "t1"
        assert block.content == "result text"

    def test_content_block_tool_result_list_content(self):
        block = ContentBlockToolResult(
            type="tool_result",
            tool_use_id="t1",
            content=[{"type": "text", "text": "result"}]
        )
        assert isinstance(block.content, list)

    def test_system_content(self):
        sc = SystemContent(type="text", text="system prompt")
        assert sc.text == "system prompt"

    def test_anthropic_message_str_content(self):
        msg = AnthropicMessage(role="user", content="hello")
        assert msg.role == "user"
        assert msg.content == "hello"

    def test_anthropic_message_list_content(self):
        msg = AnthropicMessage(
            role="assistant",
            content=[ContentBlockText(type="text", text="hi")]
        )
        assert msg.role == "assistant"

    def test_anthropic_tool(self):
        tool = AnthropicTool(name="search", description="search stuff", input_schema={"type": "object"})
        assert tool.name == "search"

    def test_anthropic_messages_request(self):
        req = AnthropicMessagesRequest(
            model="claude-3",
            messages=[AnthropicMessage(role="user", content="hi")],
        )
        assert req.model == "claude-3"
        assert req.stream is False

    def test_usage(self):
        u = Usage(input_tokens=100, output_tokens=50)
        assert u.input_tokens == 100
        assert u.cache_creation_input_tokens == 0

    def test_anthropic_messages_response(self):
        resp = AnthropicMessagesResponse(
            id="msg_1",
            model="claude-3",
            content=[ContentBlockText(type="text", text="response")],
            usage=Usage(input_tokens=10, output_tokens=20),
        )
        assert resp.role == "assistant"
        assert resp.type == "message"

    def test_token_count_request(self):
        req = TokenCountRequest(
            model="claude-3",
            messages=[AnthropicMessage(role="user", content="hi")],
        )
        assert req.model == "claude-3"

    def test_token_count_response(self):
        resp = TokenCountResponse(input_tokens=42)
        assert resp.input_tokens == 42

    def test_add_model_request(self):
        req = AddModelRequest(model_data="base64data")
        assert req.model_data == "base64data"
        assert req.config_data is None

    def test_add_model_response(self):
        resp = AddModelResponse(model="model-key")
        assert resp.model == "model-key"

    def test_delete_model_request(self):
        req = DeleteModelRequest(model_key="key1")
        assert req.model_key == "key1"

    def test_delete_model_response(self):
        resp = DeleteModelResponse(success=True, message="ok")
        assert resp.success


# ---------------------------------------------------------------------------
# AnthropicProxyApp Init
# ---------------------------------------------------------------------------

class TestAnthropicProxyAppInit:
    def test_init_defaults(self):
        app = AnthropicProxyApp()
        assert app.models == {}
        assert app.model_configs == {}
        assert app.claude_models == {}
        assert app.app is not None

    def test_init_with_claude_models(self):
        mock_model = MagicMock(spec=LLMModel)
        mock_model.generate_content_config = None
        app = AnthropicProxyApp(claude_models={"sonnet": mock_model})
        assert "sonnet" in app.claude_models

    def test_init_extracts_config_from_llm_models(self):
        mock_model = MagicMock(spec=LLMModel)
        mock_config = MagicMock(spec=GenerateContentConfig)
        mock_model.generate_content_config = mock_config
        app = AnthropicProxyApp(claude_models={"sonnet": mock_model})
        assert "sonnet" in app.model_configs

    def test_init_skips_config_for_factories(self):
        async def factory():
            return MagicMock(spec=LLMModel)
        app = AnthropicProxyApp(claude_models={"sonnet": factory})
        assert "sonnet" not in app.model_configs


# ---------------------------------------------------------------------------
# _resolve_model
# ---------------------------------------------------------------------------

class TestResolveModel:
    async def test_exact_match_dynamic(self):
        app = AnthropicProxyApp()
        mock_model = MagicMock(spec=LLMModel)
        app.models["my-model"] = mock_model

        result = await app._resolve_model("my-model")
        assert result is mock_model

    async def test_pattern_match_sonnet(self):
        mock_model = MagicMock(spec=LLMModel)
        mock_model.generate_content_config = None
        app = AnthropicProxyApp(claude_models={"sonnet": mock_model})

        result = await app._resolve_model("claude-sonnet-4-20250514")
        assert result is mock_model

    async def test_pattern_match_opus(self):
        mock_model = MagicMock(spec=LLMModel)
        mock_model.generate_content_config = None
        app = AnthropicProxyApp(claude_models={"opus": mock_model})

        result = await app._resolve_model("claude-opus-3-latest")
        assert result is mock_model

    async def test_pattern_match_haiku(self):
        mock_model = MagicMock(spec=LLMModel)
        mock_model.generate_content_config = None
        app = AnthropicProxyApp(claude_models={"haiku": mock_model})

        result = await app._resolve_model("claude-3-haiku-20240307")
        assert result is mock_model

    async def test_no_match_returns_none(self):
        app = AnthropicProxyApp()
        result = await app._resolve_model("unknown-model")
        assert result is None

    async def test_callable_factory(self):
        mock_model = MagicMock(spec=LLMModel)
        mock_model.generate_content_config = None

        async def factory():
            return mock_model

        app = AnthropicProxyApp(claude_models={"sonnet": factory})
        result = await app._resolve_model("claude-sonnet-4-20250514")
        assert result is mock_model

    async def test_callable_factory_extracts_config(self):
        mock_config = MagicMock(spec=GenerateContentConfig)
        mock_model = MagicMock(spec=LLMModel)
        mock_model.generate_content_config = mock_config

        async def factory():
            return mock_model

        app = AnthropicProxyApp(claude_models={"sonnet": factory})
        await app._resolve_model("claude-sonnet-4-20250514")
        assert "sonnet" in app.model_configs


# ---------------------------------------------------------------------------
# _convert_dict_to_schema
# ---------------------------------------------------------------------------

class TestConvertDictToSchema:
    def test_empty_dict(self):
        app = AnthropicProxyApp()
        schema = app._convert_dict_to_schema({})
        assert isinstance(schema, Schema)

    def test_with_type(self):
        app = AnthropicProxyApp()
        schema = app._convert_dict_to_schema({"type": "object"})
        assert schema.type == "object"

    def test_with_description(self):
        app = AnthropicProxyApp()
        schema = app._convert_dict_to_schema({"description": "test desc"})
        assert schema.description == "test desc"

    def test_with_properties(self):
        app = AnthropicProxyApp()
        schema = app._convert_dict_to_schema({
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            }
        })
        assert "name" in schema.properties
        assert "age" in schema.properties

    def test_with_required(self):
        app = AnthropicProxyApp()
        schema = app._convert_dict_to_schema({
            "type": "object",
            "required": ["name"],
        })
        assert schema.required == ["name"]

    def test_with_items(self):
        app = AnthropicProxyApp()
        schema = app._convert_dict_to_schema({
            "type": "array",
            "items": {"type": "string"},
        })
        assert schema.items is not None
        assert schema.items.type == "string"

    def test_with_additional_properties(self):
        app = AnthropicProxyApp()
        schema = app._convert_dict_to_schema({
            "type": "object",
            "additionalProperties": True,
        })
        assert schema.additional_properties is True


# ---------------------------------------------------------------------------
# _convert_anthropic_to_llm_request
# ---------------------------------------------------------------------------

class TestConvertAnthropicToLlmRequest:
    def _make_app(self):
        return AnthropicProxyApp()

    def test_simple_text_message(self):
        app = self._make_app()
        request = AnthropicMessagesRequest(
            model="test-model",
            messages=[AnthropicMessage(role="user", content="hello")],
        )
        model = MagicMock(spec=LLMModel)
        model.generate_content_config = None

        llm_req = app._convert_anthropic_to_llm_request(request, model)
        assert len(llm_req.contents) == 1
        assert llm_req.contents[0].parts[0].text == "hello"

    def test_complex_content_blocks(self):
        app = self._make_app()
        request = AnthropicMessagesRequest(
            model="test-model",
            messages=[
                AnthropicMessage(
                    role="user",
                    content=[
                        ContentBlockText(type="text", text="check this"),
                        ContentBlockToolUse(type="tool_use", id="t1", name="search", input={"q": "test"}),
                    ],
                )
            ],
        )
        model = MagicMock(spec=LLMModel)
        model.generate_content_config = None

        llm_req = app._convert_anthropic_to_llm_request(request, model)
        parts = llm_req.contents[0].parts
        assert parts[0].text == "check this"
        assert parts[1].function_call.name == "search"

    def test_tool_result_string_content(self):
        app = self._make_app()
        request = AnthropicMessagesRequest(
            model="test-model",
            messages=[
                AnthropicMessage(
                    role="user",
                    content=[
                        ContentBlockToolResult(type="tool_result", tool_use_id="t1", content="result text"),
                    ],
                )
            ],
        )
        model = MagicMock(spec=LLMModel)
        model.generate_content_config = None

        llm_req = app._convert_anthropic_to_llm_request(request, model)
        part = llm_req.contents[0].parts[0]
        assert part.function_response is not None
        assert part.function_response.response == {"result": "result text"}

    def test_tool_result_list_content(self):
        app = self._make_app()
        request = AnthropicMessagesRequest(
            model="test-model",
            messages=[
                AnthropicMessage(
                    role="user",
                    content=[
                        ContentBlockToolResult(
                            type="tool_result",
                            tool_use_id="t1",
                            content=[{"type": "text", "text": "line1"}, {"type": "text", "text": "line2"}],
                        ),
                    ],
                )
            ],
        )
        model = MagicMock(spec=LLMModel)
        model.generate_content_config = None

        llm_req = app._convert_anthropic_to_llm_request(request, model)
        part = llm_req.contents[0].parts[0]
        assert "line1" in part.function_response.response["result"]

    def test_tool_result_dict_content(self):
        app = self._make_app()
        request = AnthropicMessagesRequest(
            model="test-model",
            messages=[
                AnthropicMessage(
                    role="user",
                    content=[
                        ContentBlockToolResult(
                            type="tool_result",
                            tool_use_id="t1",
                            content={"key": "value"},
                        ),
                    ],
                )
            ],
        )
        model = MagicMock(spec=LLMModel)
        model.generate_content_config = None

        llm_req = app._convert_anthropic_to_llm_request(request, model)
        part = llm_req.contents[0].parts[0]
        assert part.function_response.response == {"key": "value"}

    def test_system_string(self):
        app = self._make_app()
        request = AnthropicMessagesRequest(
            model="test-model",
            messages=[AnthropicMessage(role="user", content="hello")],
            system="You are helpful",
        )
        model = MagicMock(spec=LLMModel)
        model.generate_content_config = None

        llm_req = app._convert_anthropic_to_llm_request(request, model)
        assert llm_req.config.system_instruction == "You are helpful"

    def test_system_list(self):
        app = self._make_app()
        request = AnthropicMessagesRequest(
            model="test-model",
            messages=[AnthropicMessage(role="user", content="hello")],
            system=[
                SystemContent(type="text", text="First part"),
                SystemContent(type="text", text="Second part"),
            ],
        )
        model = MagicMock(spec=LLMModel)
        model.generate_content_config = None

        llm_req = app._convert_anthropic_to_llm_request(request, model)
        assert "First part" in llm_req.config.system_instruction
        assert "Second part" in llm_req.config.system_instruction

    def test_temperature_and_max_tokens(self):
        app = self._make_app()
        request = AnthropicMessagesRequest(
            model="test-model",
            messages=[AnthropicMessage(role="user", content="hello")],
            temperature=0.7,
            max_tokens=1000,
        )
        model = MagicMock(spec=LLMModel)
        model.generate_content_config = None

        llm_req = app._convert_anthropic_to_llm_request(request, model)
        assert llm_req.config.temperature == 0.7
        assert llm_req.config.max_output_tokens == 1000

    def test_top_p_and_top_k(self):
        app = self._make_app()
        request = AnthropicMessagesRequest(
            model="test-model",
            messages=[AnthropicMessage(role="user", content="hello")],
            top_p=0.9,
            top_k=40,
        )
        model = MagicMock(spec=LLMModel)
        model.generate_content_config = None

        llm_req = app._convert_anthropic_to_llm_request(request, model)
        assert llm_req.config.top_p == 0.9
        assert llm_req.config.top_k == 40

    def test_tools_conversion(self):
        app = self._make_app()
        request = AnthropicMessagesRequest(
            model="test-model",
            messages=[AnthropicMessage(role="user", content="hello")],
            tools=[
                AnthropicTool(
                    name="search",
                    description="Search for stuff",
                    input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
                )
            ],
        )
        model = MagicMock(spec=LLMModel)
        model.generate_content_config = None

        llm_req = app._convert_anthropic_to_llm_request(request, model)
        assert llm_req.config.tools is not None
        assert len(llm_req.config.tools) == 1

    def test_stored_config_takes_precedence(self):
        app = self._make_app()
        stored = GenerateContentConfig(temperature=0.3, max_output_tokens=500)
        app.model_configs["test-model"] = stored

        request = AnthropicMessagesRequest(
            model="test-model",
            messages=[AnthropicMessage(role="user", content="hello")],
            temperature=0.9,
            max_tokens=2000,
        )
        model = MagicMock(spec=LLMModel)
        model.generate_content_config = None

        llm_req = app._convert_anthropic_to_llm_request(request, model)
        # Stored config takes precedence
        assert llm_req.config.temperature == 0.3
        assert llm_req.config.max_output_tokens == 500

    def test_streaming_sets_tool_names(self):
        app = self._make_app()
        request = AnthropicMessagesRequest(
            model="test-model",
            messages=[AnthropicMessage(role="user", content="hello")],
            stream=True,
            tools=[
                AnthropicTool(name="tool1", input_schema={"type": "object"}),
            ],
        )
        model = MagicMock(spec=LLMModel)
        model.generate_content_config = None

        llm_req = app._convert_anthropic_to_llm_request(request, model)
        assert llm_req.streaming_tool_names is not None
        assert "tool1" in llm_req.streaming_tool_names

    def test_image_content_block_logged(self):
        app = self._make_app()
        request = AnthropicMessagesRequest(
            model="test-model",
            messages=[
                AnthropicMessage(
                    role="user",
                    content=[
                        ContentBlockImage(type="image", source={"type": "base64", "data": "abc"}),
                    ],
                )
            ],
        )
        model = MagicMock(spec=LLMModel)
        model.generate_content_config = None

        llm_req = app._convert_anthropic_to_llm_request(request, model)
        # Image blocks are skipped, so contents should be empty or have no parts
        assert len(llm_req.contents) == 0

    def test_stop_sequences(self):
        app = self._make_app()
        request = AnthropicMessagesRequest(
            model="test-model",
            messages=[AnthropicMessage(role="user", content="hello")],
            stop_sequences=["STOP"],
        )
        model = MagicMock(spec=LLMModel)
        model.generate_content_config = None

        llm_req = app._convert_anthropic_to_llm_request(request, model)
        assert llm_req.config.stop_sequences == ["STOP"]

    def test_model_instance_config_fallback(self):
        app = self._make_app()
        model_config = GenerateContentConfig(temperature=0.5)
        model = MagicMock(spec=LLMModel)
        model.generate_content_config = model_config

        request = AnthropicMessagesRequest(
            model="test-model",
            messages=[AnthropicMessage(role="user", content="hello")],
        )

        llm_req = app._convert_anthropic_to_llm_request(request, model)
        assert llm_req.config.temperature == 0.5


# ---------------------------------------------------------------------------
# _convert_llm_response_to_anthropic
# ---------------------------------------------------------------------------

class TestConvertLlmResponseToAnthropic:
    def test_text_response(self):
        app = AnthropicProxyApp()
        llm_resp = LlmResponse(
            content=Content(parts=[Part.from_text(text="Hello!")]),
        )
        request = AnthropicMessagesRequest(
            model="test",
            messages=[AnthropicMessage(role="user", content="hi")],
        )
        result = app._convert_llm_response_to_anthropic(llm_resp, request)
        assert len(result.content) == 1
        assert result.content[0].type == "text"
        assert result.content[0].text == "Hello!"
        assert result.stop_reason == "end_turn"

    def test_tool_use_response(self):
        app = AnthropicProxyApp()
        fc = Part.from_function_call(name="search", args={"q": "test"})
        fc.function_call.id = "call_123"
        llm_resp = LlmResponse(
            content=Content(parts=[fc]),
        )
        request = AnthropicMessagesRequest(
            model="test",
            messages=[AnthropicMessage(role="user", content="hi")],
        )
        result = app._convert_llm_response_to_anthropic(llm_resp, request)
        assert any(isinstance(b, ContentBlockToolUse) for b in result.content)
        assert result.stop_reason == "tool_use"

    def test_empty_response_gets_placeholder(self):
        app = AnthropicProxyApp()
        llm_resp = LlmResponse(content=Content(parts=[]))
        request = AnthropicMessagesRequest(
            model="test",
            messages=[AnthropicMessage(role="user", content="hi")],
        )
        result = app._convert_llm_response_to_anthropic(llm_resp, request)
        assert len(result.content) == 1
        assert result.content[0].text == ""

    def test_usage_extraction(self):
        from google.genai.types import GenerateContentResponseUsageMetadata
        app = AnthropicProxyApp()
        usage = GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=50)
        llm_resp = LlmResponse(
            content=Content(parts=[Part.from_text(text="hi")]),
            usage_metadata=usage,
        )
        request = AnthropicMessagesRequest(
            model="test",
            messages=[AnthropicMessage(role="user", content="hi")],
        )
        result = app._convert_llm_response_to_anthropic(llm_resp, request)
        assert result.usage.input_tokens == 100
        assert result.usage.output_tokens == 50

    def test_max_tokens_stop_reason(self):
        app = AnthropicProxyApp()
        llm_resp = LlmResponse(
            content=Content(parts=[Part.from_text(text="truncated")]),
            error_code="length",
        )
        request = AnthropicMessagesRequest(
            model="test",
            messages=[AnthropicMessage(role="user", content="hi")],
        )
        result = app._convert_llm_response_to_anthropic(llm_resp, request)
        assert result.stop_reason == "max_tokens"

    def test_tool_calls_stop_reason_from_error_code(self):
        app = AnthropicProxyApp()
        llm_resp = LlmResponse(
            content=Content(parts=[Part.from_text(text="result")]),
            error_code="tool_calls",
        )
        request = AnthropicMessagesRequest(
            model="test",
            messages=[AnthropicMessage(role="user", content="hi")],
        )
        result = app._convert_llm_response_to_anthropic(llm_resp, request)
        assert result.stop_reason == "tool_use"

    def test_none_content(self):
        app = AnthropicProxyApp()
        llm_resp = LlmResponse(content=None)
        request = AnthropicMessagesRequest(
            model="test",
            messages=[AnthropicMessage(role="user", content="hi")],
        )
        result = app._convert_llm_response_to_anthropic(llm_resp, request)
        assert len(result.content) == 1
        assert result.content[0].text == ""


# ---------------------------------------------------------------------------
# _handle_non_streaming
# ---------------------------------------------------------------------------

class TestHandleNonStreaming:
    async def test_successful_response(self):
        app = AnthropicProxyApp()
        model = MagicMock(spec=LLMModel)
        model.generate_content_config = None

        llm_resp = LlmResponse(
            content=Content(parts=[Part.from_text(text="response")]),
        )

        async def mock_generate(req, stream=False):
            yield llm_resp

        model.generate_async = mock_generate

        request = AnthropicMessagesRequest(
            model="test",
            messages=[AnthropicMessage(role="user", content="hi")],
        )
        llm_request = app._convert_anthropic_to_llm_request(request, model)

        result = await app._handle_non_streaming(llm_request, request, model)
        assert result.content[0].text == "response"

    async def test_no_response_raises(self):
        app = AnthropicProxyApp()
        model = MagicMock(spec=LLMModel)

        async def mock_generate(req, stream=False):
            return
            yield  # Make it an async generator

        model.generate_async = mock_generate

        request = AnthropicMessagesRequest(
            model="test",
            messages=[AnthropicMessage(role="user", content="hi")],
        )
        llm_request = MagicMock(spec=LlmRequest)

        with pytest.raises(ValueError, match="No response from model"):
            await app._handle_non_streaming(llm_request, request, model)

    async def test_error_response_raises(self):
        from fastapi import HTTPException
        app = AnthropicProxyApp()
        model = MagicMock(spec=LLMModel)

        llm_resp = LlmResponse(
            content=Content(parts=[Part.from_text(text="error")]),
            error_code="500",
            error_message="Internal error",
        )

        async def mock_generate(req, stream=False):
            yield llm_resp

        model.generate_async = mock_generate

        request = AnthropicMessagesRequest(
            model="test",
            messages=[AnthropicMessage(role="user", content="hi")],
        )
        llm_request = MagicMock(spec=LlmRequest)

        with pytest.raises(HTTPException):
            await app._handle_non_streaming(llm_request, request, model)


# ---------------------------------------------------------------------------
# _handle_streaming
# ---------------------------------------------------------------------------

class TestHandleStreaming:
    async def test_basic_streaming(self):
        app = AnthropicProxyApp()
        model = MagicMock(spec=LLMModel)

        partial_resp = LlmResponse(
            content=Content(parts=[Part.from_text(text="chunk")]),
            partial=True,
        )
        final_resp = LlmResponse(
            content=Content(parts=[Part.from_text(text="final")]),
            partial=False,
        )

        async def mock_generate(req, stream=True):
            yield partial_resp
            yield final_resp

        model.generate_async = mock_generate

        request = AnthropicMessagesRequest(
            model="test",
            messages=[AnthropicMessage(role="user", content="hi")],
            stream=True,
        )
        llm_request = MagicMock(spec=LlmRequest)

        events = []
        async for event in app._handle_streaming(llm_request, request, model):
            events.append(event)

        # Should contain: message_start, content_block_start, ping, text_delta, content_block_stop, message_delta, message_stop, [DONE]
        event_texts = "".join(events)
        assert "message_start" in event_texts
        assert "text_delta" in event_texts
        assert "message_stop" in event_texts
        assert "[DONE]" in event_texts

    async def test_streaming_error(self):
        app = AnthropicProxyApp()
        model = MagicMock(spec=LLMModel)

        async def mock_generate(req, stream=True):
            raise RuntimeError("streaming error")
            yield  # noqa: make it an async generator

        model.generate_async = mock_generate

        request = AnthropicMessagesRequest(
            model="test",
            messages=[AnthropicMessage(role="user", content="hi")],
            stream=True,
        )
        llm_request = MagicMock(spec=LlmRequest)

        events = []
        async for event in app._handle_streaming(llm_request, request, model):
            events.append(event)

        event_texts = "".join(events)
        assert "error" in event_texts.lower() or "message_stop" in event_texts

    async def test_streaming_with_tool_calls(self):
        from trpc_agent_sdk.models import TOOL_STREAMING_ARGS
        app = AnthropicProxyApp()
        model = MagicMock(spec=LLMModel)

        # Simulate streaming tool call + final response
        partial_tool = LlmResponse(
            content=Content(parts=[Part.from_function_call(
                name="search",
                args={TOOL_STREAMING_ARGS: '{"q":"test'}
            )]),
            partial=True,
        )
        partial_tool.content.parts[0].function_call.id = "tool_1"

        final_resp = LlmResponse(
            content=Content(parts=[Part.from_function_call(
                name="search",
                args={"q": "test"}
            )]),
            partial=False,
        )
        final_resp.content.parts[0].function_call.id = "tool_1"

        async def mock_generate(req, stream=True):
            yield partial_tool
            yield final_resp

        model.generate_async = mock_generate

        request = AnthropicMessagesRequest(
            model="test",
            messages=[AnthropicMessage(role="user", content="hi")],
            stream=True,
            tools=[AnthropicTool(name="search", input_schema={"type": "object"})],
        )
        llm_request = MagicMock(spec=LlmRequest)
        llm_request.streaming_tool_names = {"search"}

        events = []
        async for event in app._handle_streaming(llm_request, request, model):
            events.append(event)

        event_texts = "".join(events)
        assert "tool_use" in event_texts
        assert "content_block_start" in event_texts

    async def test_streaming_with_non_streamed_tool_call(self):
        app = AnthropicProxyApp()
        model = MagicMock(spec=LLMModel)

        final_resp = LlmResponse(
            content=Content(parts=[Part.from_function_call(
                name="calculator",
                args={"expression": "2+2"}
            )]),
            partial=False,
        )
        final_resp.content.parts[0].function_call.id = "tool_2"

        async def mock_generate(req, stream=True):
            yield final_resp

        model.generate_async = mock_generate

        request = AnthropicMessagesRequest(
            model="test",
            messages=[AnthropicMessage(role="user", content="hi")],
            stream=True,
        )
        llm_request = MagicMock(spec=LlmRequest)

        events = []
        async for event in app._handle_streaming(llm_request, request, model):
            events.append(event)

        event_texts = "".join(events)
        assert "tool_use" in event_texts
        assert "message_stop" in event_texts

    async def test_streaming_max_tokens_stop_reason(self):
        app = AnthropicProxyApp()
        model = MagicMock(spec=LLMModel)

        final_resp = LlmResponse(
            content=Content(parts=[Part.from_text(text="truncated")]),
            partial=False,
            error_code="length",
        )

        async def mock_generate(req, stream=True):
            yield final_resp

        model.generate_async = mock_generate

        request = AnthropicMessagesRequest(
            model="test",
            messages=[AnthropicMessage(role="user", content="hi")],
            stream=True,
        )
        llm_request = MagicMock(spec=LlmRequest)

        events = []
        async for event in app._handle_streaming(llm_request, request, model):
            events.append(event)

        event_texts = "".join(events)
        assert "max_tokens" in event_texts


# ---------------------------------------------------------------------------
# Route Tests (using httpx TestClient)
# ---------------------------------------------------------------------------

class TestRoutes:
    def _make_app_and_model(self):
        """Create an app with a model installed."""
        app = AnthropicProxyApp()
        mock_model = MagicMock(spec=LLMModel)
        mock_model.generate_content_config = None
        app.models["test-model"] = mock_model
        return app, mock_model

    async def test_root_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        app = AnthropicProxyApp()
        transport = ASGITransport(app=app.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/")
        assert resp.status_code == 200
        assert "Anthropic API Proxy Server" in resp.json()["message"]

    async def test_create_message_non_streaming(self):
        from httpx import AsyncClient, ASGITransport
        app, mock_model = self._make_app_and_model()

        llm_resp = LlmResponse(
            content=Content(parts=[Part.from_text(text="Hello back!")]),
        )

        async def mock_generate(req, stream=False):
            yield llm_resp

        mock_model.generate_async = mock_generate

        transport = ASGITransport(app=app.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/messages", json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hello"}],
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "assistant"

    async def test_create_message_model_not_found(self):
        from httpx import AsyncClient, ASGITransport
        app = AnthropicProxyApp()

        transport = ASGITransport(app=app.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/messages", json={
                "model": "nonexistent",
                "messages": [{"role": "user", "content": "Hello"}],
            })
        assert resp.status_code == 500

    async def test_count_tokens(self):
        from httpx import AsyncClient, ASGITransport
        app, mock_model = self._make_app_and_model()

        transport = ASGITransport(app=app.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/messages/count_tokens", json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hello world test message"}],
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "input_tokens" in data
        assert data["input_tokens"] > 0

    async def test_count_tokens_model_not_found(self):
        from httpx import AsyncClient, ASGITransport
        app = AnthropicProxyApp()

        transport = ASGITransport(app=app.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/messages/count_tokens", json={
                "model": "nonexistent",
                "messages": [{"role": "user", "content": "Hello"}],
            })
        assert resp.status_code == 500

    async def test_add_model_endpoint(self):
        import base64
        import cloudpickle as pickle
        from httpx import AsyncClient, ASGITransport

        app = AnthropicProxyApp()

        # Use a picklable mock by patching isinstance check inside the endpoint
        class _FakeModel:
            name = "test-model"

        model = _FakeModel()
        pickled = pickle.dumps(model)
        encoded = base64.b64encode(pickled).decode("ascii")

        transport = ASGITransport(app=app.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # The endpoint will reject non-LLMModel, so we patch isinstance
            with patch("trpc_agent_sdk.server.agents.claude._proxy.isinstance", side_effect=lambda obj, cls: True):
                resp = await client.post("/add_model", json={"model_data": encoded})
        assert resp.status_code == 200
        data = resp.json()
        assert "model" in data
        assert len(app.models) == 1

    async def test_add_model_with_config(self):
        import base64
        import cloudpickle as pickle
        from httpx import AsyncClient, ASGITransport

        app = AnthropicProxyApp()

        class _FakeModel:
            name = "test-model"

        model = _FakeModel()
        mock_config = {"temperature": 0.5}  # Simple picklable config

        pickled_model = pickle.dumps(model)
        pickled_config = pickle.dumps(mock_config)
        encoded_model = base64.b64encode(pickled_model).decode("ascii")
        encoded_config = base64.b64encode(pickled_config).decode("ascii")

        transport = ASGITransport(app=app.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("trpc_agent_sdk.server.agents.claude._proxy.isinstance", side_effect=lambda obj, cls: True):
                resp = await client.post("/add_model", json={
                    "model_data": encoded_model,
                    "config_data": encoded_config,
                })
        assert resp.status_code == 200
        assert len(app.model_configs) == 1

    async def test_delete_model_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        app = AnthropicProxyApp()
        mock_model = MagicMock(spec=LLMModel)
        app.models["model-key"] = mock_model

        transport = ASGITransport(app=app.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/delete_model", json={"model_key": "model-key"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(app.models) == 0

    async def test_delete_model_not_found(self):
        from httpx import AsyncClient, ASGITransport
        app = AnthropicProxyApp()

        transport = ASGITransport(app=app.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/delete_model", json={"model_key": "nonexistent"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False

    async def test_delete_model_with_config(self):
        from httpx import AsyncClient, ASGITransport
        app = AnthropicProxyApp()
        app.models["key1"] = MagicMock(spec=LLMModel)
        app.model_configs["key1"] = GenerateContentConfig()

        transport = ASGITransport(app=app.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/delete_model", json={"model_key": "key1"})
        assert resp.status_code == 200
        assert "key1" not in app.model_configs

    async def test_count_tokens_with_tools(self):
        from httpx import AsyncClient, ASGITransport
        app, mock_model = self._make_app_and_model()

        transport = ASGITransport(app=app.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/messages/count_tokens", json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hello"}],
                "tools": [{"name": "search", "input_schema": {"type": "object"}}],
            })
        assert resp.status_code == 200

    async def test_count_tokens_with_system(self):
        from httpx import AsyncClient, ASGITransport
        app, mock_model = self._make_app_and_model()

        transport = ASGITransport(app=app.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/messages/count_tokens", json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hello"}],
                "system": "You are helpful",
            })
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Additional conversion tests for uncovered branches
# ---------------------------------------------------------------------------

class TestConvertAnthropicEdgeCases:
    def test_stored_config_fills_missing_fields(self):
        app = AnthropicProxyApp()
        stored = GenerateContentConfig()  # All None
        app.model_configs["test-model"] = stored

        request = AnthropicMessagesRequest(
            model="test-model",
            messages=[AnthropicMessage(role="user", content="hello")],
            temperature=0.9,
            max_tokens=2000,
            top_p=0.95,
            top_k=40,
            stop_sequences=["STOP"],
        )
        model = MagicMock(spec=LLMModel)
        model.generate_content_config = None

        llm_req = app._convert_anthropic_to_llm_request(request, model)
        assert llm_req.config.temperature == 0.9
        assert llm_req.config.max_output_tokens == 2000
        assert llm_req.config.top_p == 0.95
        assert llm_req.config.top_k == 40
        assert llm_req.config.stop_sequences == ["STOP"]

    def test_tool_result_with_string_items_in_list(self):
        app = AnthropicProxyApp()
        request = AnthropicMessagesRequest(
            model="test-model",
            messages=[
                AnthropicMessage(
                    role="user",
                    content=[
                        ContentBlockToolResult(
                            type="tool_result",
                            tool_use_id="t1",
                            content=[{"type": "other", "data": "misc"}],
                        ),
                    ],
                )
            ],
        )
        model = MagicMock(spec=LLMModel)
        model.generate_content_config = None

        llm_req = app._convert_anthropic_to_llm_request(request, model)
        assert len(llm_req.contents) == 1
