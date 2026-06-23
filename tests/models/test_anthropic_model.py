# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for Anthropic model implementation."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import anthropic
import httpx
import pytest
from anthropic import types as anthropic_types
from trpc_agent_sdk.models import AnthropicModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Tool
from trpc_agent_sdk.types import Type


class TestAnthropicModelInitialization:
    """Test AnthropicModel initialization."""

    def test_basic_initialization(self):
        """Test basic model initialization."""
        model = AnthropicModel(
            model_name="claude-3-5-sonnet-20241022",
            api_key="test-api-key",
        )

        assert model._model_name == "claude-3-5-sonnet-20241022"
        assert model._api_key == "test-api-key"

    def test_initialization_with_config(self):
        """Test model initialization with default config."""
        config = GenerateContentConfig(temperature=0.5, max_output_tokens=1000)
        model = AnthropicModel(
            model_name="claude-3-5-sonnet-20241022",
            api_key="test-api-key",
            generate_content_config=config,
        )

        assert model.generate_content_config == config
        assert model.generate_content_config.temperature == 0.5
        assert model.generate_content_config.max_output_tokens == 1000


class TestAnthropicModelValidation:
    """Test request validation."""

    def test_validate_empty_contents(self):
        """Test validation fails for empty contents."""
        model = AnthropicModel(model_name="claude-3-5-sonnet-20241022", api_key="test-key")
        request = LlmRequest(contents=[])

        with pytest.raises(ValueError, match="At least one content is required"):
            model.validate_request(request)

    def test_validate_empty_parts(self):
        """Test validation fails for empty parts."""
        model = AnthropicModel(model_name="claude-3-5-sonnet-20241022", api_key="test-key")
        request = LlmRequest(contents=[Content(parts=[], role="user")])

        with pytest.raises(ValueError, match="Content must have at least one part"):
            model.validate_request(request)

    def test_validate_invalid_role(self):
        """Test validation fails for invalid role."""
        model = AnthropicModel(model_name="claude-3-5-sonnet-20241022", api_key="test-key")
        request = LlmRequest(contents=[Content(parts=[Part.from_text(text="Hello")], role="invalid_role")])

        with pytest.raises(ValueError, match="Invalid content role"):
            model.validate_request(request)

    def test_validate_valid_request(self):
        """Test validation passes for valid request."""
        model = AnthropicModel(model_name="claude-3-5-sonnet-20241022", api_key="test-key")
        request = LlmRequest(contents=[Content(parts=[Part.from_text(text="Hello")], role="user")])

        # Should not raise any exception
        model.validate_request(request)


class TestAnthropicModelMessageFormatting:
    """Test message formatting."""

    def test_format_simple_text_message(self):
        """Test formatting simple text message."""
        model = AnthropicModel(model_name="claude-3-5-sonnet-20241022", api_key="test-key")
        request = LlmRequest(contents=[Content(parts=[Part.from_text(text="Hello, Claude!")], role="user")])

        messages = model._format_messages(request)

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert len(messages[0]["content"]) == 1
        assert messages[0]["content"][0]["type"] == "text"
        assert messages[0]["content"][0]["text"] == "Hello, Claude!"

    def test_format_assistant_message(self):
        """Test formatting assistant message."""
        model = AnthropicModel(model_name="claude-3-5-sonnet-20241022", api_key="test-key")
        request = LlmRequest(contents=[
            Content(parts=[Part.from_text(text="User message")], role="user"),
            Content(parts=[Part.from_text(text="Assistant response")], role="model"),
        ])

        messages = model._format_messages(request)

        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"][0]["text"] == "Assistant response"

    def test_format_function_call(self):
        """Test formatting function call message."""
        model = AnthropicModel(model_name="claude-3-5-sonnet-20241022", api_key="test-key")
        part = Part.from_function_call(name="get_weather", args={"location": "San Francisco"})
        part.function_call.id = "call_123"  # type: ignore
        request = LlmRequest(contents=[Content(parts=[part], role="model")])

        messages = model._format_messages(request)

        assert len(messages) == 1
        assert messages[0]["role"] == "assistant"
        assert len(messages[0]["content"]) == 1
        assert messages[0]["content"][0]["type"] == "tool_use"
        assert messages[0]["content"][0]["name"] == "get_weather"
        assert messages[0]["content"][0]["input"] == {"location": "San Francisco"}

    def test_format_function_response(self):
        """Test formatting function response message."""
        model = AnthropicModel(model_name="claude-3-5-sonnet-20241022", api_key="test-key")
        part = Part.from_function_response(name="get_weather",
                                           response={"result": {
                                               "temperature": 72,
                                               "condition": "sunny"
                                           }})
        part.function_response.id = "call_123"  # type: ignore
        request = LlmRequest(contents=[Content(parts=[part], role="user")])

        messages = model._format_messages(request)

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert len(messages[0]["content"]) == 1
        assert messages[0]["content"][0]["type"] == "tool_result"
        assert messages[0]["content"][0]["tool_use_id"] == "call_123"


class TestAnthropicModelToolConversion:
    """Test tool conversion."""

    def test_convert_simple_tool(self):
        """Test converting simple tool to Anthropic format."""
        model = AnthropicModel(model_name="claude-3-5-sonnet-20241022", api_key="test-key")

        # Create a simple function declaration
        func_decl = FunctionDeclaration(
            name="get_weather",
            description="Get the weather for a location",
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "location": Schema(type=Type.STRING, description="The city name"),
                    "unit": Schema(type=Type.STRING, description="Temperature unit (celsius/fahrenheit)"),
                },
                required=["location"],
            ),
        )
        tool = Tool(function_declarations=[func_decl])

        anthropic_tools = model._convert_tools_to_anthropic_format([tool])

        assert len(anthropic_tools) == 1
        assert anthropic_tools[0]["name"] == "get_weather"
        assert anthropic_tools[0]["description"] == "Get the weather for a location"
        assert "location" in anthropic_tools[0]["input_schema"]["properties"]
        assert "unit" in anthropic_tools[0]["input_schema"]["properties"]
        assert anthropic_tools[0]["input_schema"]["required"] == ["location"]

    def test_convert_multiple_tools(self):
        """Test converting multiple tools."""
        model = AnthropicModel(model_name="claude-3-5-sonnet-20241022", api_key="test-key")

        func_decl1 = FunctionDeclaration(
            name="get_weather",
            description="Get weather",
            parameters=Schema(type=Type.OBJECT, properties={}),
        )
        func_decl2 = FunctionDeclaration(name="calculate",
                                         description="Calculate",
                                         parameters=Schema(type=Type.OBJECT, properties={}))
        tool = Tool(function_declarations=[func_decl1, func_decl2])

        anthropic_tools = model._convert_tools_to_anthropic_format([tool])

        assert len(anthropic_tools) == 2
        assert anthropic_tools[0]["name"] == "get_weather"
        assert anthropic_tools[1]["name"] == "calculate"


class TestAnthropicModelConfigMerging:
    """Test configuration merging."""

    def test_merge_with_no_default(self):
        """Test merging when no default config exists."""
        model = AnthropicModel(model_name="claude-3-5-sonnet-20241022", api_key="test-key")
        request_config = GenerateContentConfig(temperature=0.7)

        merged = model._merge_configs(request_config)

        assert merged.temperature == 0.7

    def test_merge_with_default(self):
        """Test merging with default config."""
        default_config = GenerateContentConfig(temperature=0.5, max_output_tokens=1000)
        model = AnthropicModel(
            model_name="claude-3-5-sonnet-20241022",
            api_key="test-key",
            generate_content_config=default_config,
        )
        request_config = GenerateContentConfig(temperature=0.8)

        merged = model._merge_configs(request_config)

        # Request config should override temperature
        assert merged.temperature == 0.8
        # But default max_output_tokens should be preserved
        assert merged.max_output_tokens == 1000

    def test_merge_with_no_request_config(self):
        """Test merging when no request config provided."""
        default_config = GenerateContentConfig(temperature=0.5, max_output_tokens=1000)
        model = AnthropicModel(
            model_name="claude-3-5-sonnet-20241022",
            api_key="test-key",
            generate_content_config=default_config,
        )

        merged = model._merge_configs(None)

        # Should return default config
        assert merged.temperature == 0.5
        assert merged.max_output_tokens == 1000


class TestAnthropicModelGeneration:
    """Test content generation."""

    @pytest.mark.asyncio
    async def test_generate_simple_text(self):
        """Test generating simple text response."""
        model = AnthropicModel(model_name="claude-3-5-sonnet-20241022", api_key="test-key")

        # Mock the client and response
        mock_message = MagicMock(spec=anthropic_types.Message)
        mock_message.content = [anthropic_types.TextBlock(text="Hello! How can I help you?", type="text")]
        mock_message.usage = MagicMock(
            input_tokens=10,
            output_tokens=7,
            cache_read_input_tokens=None,
            cache_creation_input_tokens=None,
        )
        mock_message.model_dump_json = MagicMock(return_value="{}")

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_message)
        mock_client.close = AsyncMock()

        with patch.object(model, "_create_async_client", return_value=mock_client):
            request = LlmRequest(contents=[Content(parts=[Part.from_text(text="Hello")], role="user")])

            responses = []
            async for response in model.generate_async(request, stream=False):
                responses.append(response)

            assert len(responses) == 1
            assert responses[0].content is not None
            assert len(responses[0].content.parts) == 1
            assert responses[0].content.parts[0].text == "Hello! How can I help you?"
            assert responses[0].usage_metadata is not None
            assert responses[0].usage_metadata.prompt_token_count == 10
            assert responses[0].usage_metadata.candidates_token_count == 7

    @pytest.mark.asyncio
    async def test_generate_with_tool_call(self):
        """Test generating response with tool call."""
        model = AnthropicModel(model_name="claude-3-5-sonnet-20241022", api_key="test-key")

        # Mock the client and response with tool use
        mock_message = MagicMock(spec=anthropic_types.Message)
        mock_message.content = [
            anthropic_types.ToolUseBlock(
                id="tool_call_123",
                name="get_weather",
                input={"location": "San Francisco"},
                type="tool_use",
            )
        ]
        mock_message.usage = MagicMock(input_tokens=15, output_tokens=20)
        mock_message.model_dump_json = MagicMock(return_value="{}")

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_message)
        mock_client.close = AsyncMock()

        with patch.object(model, "_create_async_client", return_value=mock_client):
            # Create a tool
            func_decl = FunctionDeclaration(
                name="get_weather",
                description="Get weather",
                parameters=Schema(
                    type=Type.OBJECT,
                    properties={"location": Schema(type=Type.STRING)},
                ),
            )
            tool = Tool(function_declarations=[func_decl])

            request = LlmRequest(
                contents=[Content(parts=[Part.from_text(text="What's the weather?")], role="user")],
                config=GenerateContentConfig(tools=[tool]),
            )

            responses = []
            async for response in model.generate_async(request, stream=False):
                responses.append(response)

            assert len(responses) == 1
            assert responses[0].content is not None
            assert len(responses[0].content.parts) == 1
            assert responses[0].content.parts[0].function_call is not None
            assert responses[0].content.parts[0].function_call.name == "get_weather"
            assert responses[0].content.parts[0].function_call.args == {"location": "San Francisco"}

    @pytest.mark.asyncio
    async def test_generate_with_system_instruction(self):
        """Test generating with system instruction."""
        model = AnthropicModel(model_name="claude-3-5-sonnet-20241022", api_key="test-key")

        mock_message = MagicMock(spec=anthropic_types.Message)
        mock_message.content = [anthropic_types.TextBlock(text="Response", type="text")]
        mock_message.usage = MagicMock(input_tokens=10, output_tokens=5)
        mock_message.model_dump_json = MagicMock(return_value="{}")

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_message)
        mock_client.close = AsyncMock()

        with patch.object(model, "_create_async_client", return_value=mock_client):
            request = LlmRequest(
                contents=[Content(parts=[Part.from_text(text="Hello")], role="user")],
                config=GenerateContentConfig(system_instruction="You are a helpful assistant."),
            )

            responses = []
            async for response in model.generate_async(request, stream=False):
                responses.append(response)

            # Verify that system instruction was included in the API call
            call_args = mock_client.messages.create.call_args
            assert "system" in call_args.kwargs
            assert call_args.kwargs["system"] == "You are a helpful assistant."


class TestAnthropicModelStreaming:
    """Test streaming generation."""

    @pytest.mark.asyncio
    async def test_streaming_text(self):
        """Test streaming text generation."""
        model = AnthropicModel(model_name="claude-3-5-sonnet-20241022", api_key="test-key")

        # Create mock streaming events
        class MockStreamEvent:

            def __init__(self, event_type, **kwargs):
                self.type = event_type
                for key, value in kwargs.items():
                    setattr(self, key, value)

        class MockDelta:

            def __init__(self, delta_type, **kwargs):
                self.type = delta_type
                for key, value in kwargs.items():
                    setattr(self, key, value)

        # Simulate streaming events
        events = [
            MockStreamEvent("content_block_delta", delta=MockDelta("text_delta", text="Hello")),
            MockStreamEvent("content_block_delta", delta=MockDelta("text_delta", text=" world")),
            MockStreamEvent("content_block_delta", delta=MockDelta("text_delta", text="!")),
        ]

        # Mock the stream context manager
        mock_stream = AsyncMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)

        async def mock_aiter(self):
            for event in events:
                yield event

        mock_stream.__aiter__ = lambda self: mock_aiter(self)

        # Mock final message with content blocks
        mock_final_message = MagicMock()
        mock_final_message.usage = MagicMock(
            input_tokens=5,
            output_tokens=3,
            cache_read_input_tokens=None,
            cache_creation_input_tokens=None,
        )
        mock_final_message.content = [anthropic_types.TextBlock(text="Hello world!", type="text")]
        mock_stream.get_final_message = AsyncMock(return_value=mock_final_message)

        mock_client = AsyncMock()
        mock_client.messages.stream = MagicMock(return_value=mock_stream)
        mock_client.close = AsyncMock()

        with patch.object(model, "_create_async_client", return_value=mock_client):
            request = LlmRequest(contents=[Content(parts=[Part.from_text(text="Say hello")], role="user")])

            responses = []
            async for response in model.generate_async(request, stream=True):
                responses.append(response)

            # Should have partial responses + final complete response
            assert len(responses) >= 4  # 3 partial + 1 final

            # Check partial responses
            assert responses[0].partial is True
            assert responses[0].content.parts[0].text == "Hello"

            # Check final complete response
            final_response = responses[-1]
            assert final_response.partial is False
            assert final_response.usage_metadata is not None
            assert final_response.usage_metadata.prompt_token_count == 5
            assert final_response.usage_metadata.candidates_token_count == 3


class TestAnthropicInjectCacheControl:
    """Tests for the _inject_cache_control helper and its subordinate functions."""

    # Re-import helpers inside each test to avoid polluting the module namespace.
    @staticmethod
    def _helpers():
        from trpc_agent_sdk.models._anthropic_model import (
            _inject_cache_control,
            _apply_tools_cache_control,
            _apply_system_cache_control,
            _apply_messages_cache_control,
        )
        return _inject_cache_control, _apply_tools_cache_control, _apply_system_cache_control, _apply_messages_cache_control

    # --- tools breakpoint -------------------------------------------------

    def test_tools_stamps_last_tool_only(self):
        """Only the last tool in the list receives cache_control."""
        inject, *_ = self._helpers()
        tools = [{"name": "a"}, {"name": "b"}]
        api_params = {"tools": tools}
        inject(api_params, ["tools"], None)
        assert "cache_control" not in api_params["tools"][0]
        assert api_params["tools"][1]["cache_control"] == {"type": "ephemeral"}

    def test_tools_breakpoint_noop_when_no_tools(self):
        """No mutation when tools list is empty."""
        inject, *_ = self._helpers()
        api_params = {"tools": []}
        inject(api_params, ["tools"], None)
        assert api_params["tools"] == []

    def test_tools_breakpoint_noop_when_key_absent(self):
        """No mutation when 'tools' key is absent."""
        inject, *_ = self._helpers()
        api_params = {}
        inject(api_params, ["tools"], None)
        assert "tools" not in api_params

    # --- system breakpoint ------------------------------------------------

    def test_system_converts_string_to_text_block_with_cache_control(self):
        """system string is replaced by a text block list with cache_control."""
        inject, *_ = self._helpers()
        api_params = {"system": "You are helpful."}
        inject(api_params, ["system"], None)
        system = api_params["system"]
        assert isinstance(system, list)
        assert len(system) == 1
        assert system[0]["type"] == "text"
        assert system[0]["text"] == "You are helpful."
        assert system[0]["cache_control"] == {"type": "ephemeral"}

    def test_system_breakpoint_noop_when_key_absent(self):
        """No mutation when 'system' key is absent."""
        inject, *_ = self._helpers()
        api_params = {}
        inject(api_params, ["system"], None)
        assert "system" not in api_params

    def test_system_breakpoint_warns_and_skips_non_string_system(self):
        """Non-string system values are left unchanged instead of being stringified."""
        inject, *_ = self._helpers()
        system = [{"type": "text", "text": "sys"}]
        api_params = {"system": system}

        with patch("trpc_agent_sdk.models._anthropic_model.logger") as mock_log:
            inject(api_params, ["system"], None)

        mock_log.warning.assert_called_once()
        assert api_params["system"] is system
        assert "cache_control" not in api_params["system"][0]

    # --- messages breakpoint ----------------------------------------------

    def test_messages_stamps_last_assistant_message_last_block(self):
        """cache_control is applied to the last content block of the last assistant message."""
        inject, *_ = self._helpers()
        messages = [
            {
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": "hi"
                }]
            },
            {
                "role": "assistant",
                "content": [{
                    "type": "text",
                    "text": "hello"
                }, {
                    "type": "text",
                    "text": "bye"
                }]
            },
        ]
        api_params = {"messages": messages}
        inject(api_params, ["messages"], None)
        # last assistant message, last block should be stamped
        stamped_block = messages[1]["content"][-1]
        assert stamped_block["cache_control"] == {"type": "ephemeral"}
        # first block of assistant message is NOT stamped
        assert "cache_control" not in messages[1]["content"][0]
        # user message is NOT stamped
        assert "cache_control" not in messages[0]["content"][0]

    def test_messages_skips_latest_user_message(self):
        """When the last message is a user turn, the stamp lands on the prior assistant turn."""
        inject, *_ = self._helpers()
        messages = [
            {
                "role": "assistant",
                "content": [{
                    "type": "text",
                    "text": "answer"
                }]
            },
            {
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": "next question"
                }]
            },
        ]
        api_params = {"messages": messages}
        inject(api_params, ["messages"], None)
        # assistant message is stamped
        assert messages[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
        # user message is NOT stamped
        assert "cache_control" not in messages[1]["content"][0]

    def test_messages_noop_when_no_assistant_message(self):
        """No mutation when there is no assistant message in history."""
        inject, *_ = self._helpers()
        messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        api_params = {"messages": messages}
        inject(api_params, ["messages"], None)
        assert "cache_control" not in messages[0]["content"][0]

    def test_messages_noop_when_key_absent(self):
        inject, *_ = self._helpers()
        api_params = {}
        inject(api_params, ["messages"], None)
        assert api_params == {}

    # --- TTL handling -----------------------------------------------------

    def test_ttl_is_forwarded_in_cache_control(self):
        """TTL is provider-specific and should be forwarded inside cache_control."""
        inject, *_ = self._helpers()
        tools = [{"name": "tool1"}]
        api_params = {"tools": tools}
        inject(api_params, ["tools"], "custom-ttl")
        assert api_params["tools"][0]["cache_control"] == {
            "type": "ephemeral",
            "ttl": "custom-ttl",
        }

    def test_none_ttl_produces_minimal_cache_control(self):
        """None TTL produces cache_control with only the type field."""
        inject, *_ = self._helpers()
        tools = [{"name": "tool1"}]
        api_params = {"tools": tools}
        inject(api_params, ["tools"], None)
        assert api_params["tools"][0]["cache_control"] == {"type": "ephemeral"}

    # --- empty breakpoints ------------------------------------------------

    def test_empty_breakpoints_noop(self):
        """No changes when breakpoints list is empty."""
        inject, *_ = self._helpers()
        tools = [{"name": "tool1"}]
        api_params = {"tools": tools, "system": "sys", "messages": []}
        original_tools = [dict(t) for t in tools]
        inject(api_params, [], None)
        assert api_params["tools"] == original_tools
        assert api_params["system"] == "sys"


class TestAnthropicApplyPromptCache:
    """Tests for AnthropicModel._apply_prompt_cache delegation."""

    def test_disabled_config_leaves_api_params_unchanged(self):
        """Disabled PromptCacheConfig is a no-op."""
        from trpc_agent_sdk.configs import PromptCacheConfig
        model = AnthropicModel(
            model_name="claude-3-5-sonnet-20241022",
            api_key="k",
            prompt_cache_config=PromptCacheConfig(enabled=False),
        )
        api_params = {"tools": [{"name": "t1"}], "system": "sys"}
        model._apply_prompt_cache(api_params, None)
        assert "cache_control" not in api_params["tools"][0]
        assert isinstance(api_params["system"], str)

    def test_empty_breakpoints_leaves_api_params_unchanged(self):
        """Enabled config with no breakpoints is a no-op."""
        from trpc_agent_sdk.configs import PromptCacheConfig
        model = AnthropicModel(
            model_name="claude-3-5-sonnet-20241022",
            api_key="k",
            prompt_cache_config=PromptCacheConfig(enabled=True, breakpoints=[]),
        )
        api_params = {"system": "sys", "tools": [{"name": "t1"}]}
        model._apply_prompt_cache(api_params, None)
        assert isinstance(api_params["system"], str)
        assert "cache_control" not in api_params["tools"][0]

    def test_all_breakpoints_inject_all_points(self):
        """Enabled config with tools+system+messages injects all three breakpoints."""
        from trpc_agent_sdk.configs import PromptCacheConfig
        model = AnthropicModel(
            model_name="claude-3-5-sonnet-20241022",
            api_key="k",
            prompt_cache_config=PromptCacheConfig(
                enabled=True,
                ttl="1h",
                breakpoints=["tools", "system", "messages"],
            ),
        )
        api_params = {
            "tools": [{
                "name": "t1"
            }],
            "system":
            "You are helpful.",
            "messages": [
                {
                    "role": "assistant",
                    "content": [{
                        "type": "text",
                        "text": "previous"
                    }]
                },
                {
                    "role": "user",
                    "content": [{
                        "type": "text",
                        "text": "new question"
                    }]
                },
            ],
        }
        model._apply_prompt_cache(api_params, None)
        # tools stamped
        assert api_params["tools"][0]["cache_control"]["type"] == "ephemeral"
        assert api_params["tools"][0]["cache_control"]["ttl"] == "1h"
        # system converted to list
        assert isinstance(api_params["system"], list)
        assert api_params["system"][0]["cache_control"]["type"] == "ephemeral"
        # assistant message stamped
        assert api_params["messages"][0]["content"][0]["cache_control"]["type"] == "ephemeral"


class TestAnthropicModelRetryErrors:

    @pytest.mark.asyncio
    async def test_generate_single_error_raises_and_closes_client(self):
        model = AnthropicModel(model_name="claude-3-5-sonnet-20241022", api_key="test-key")
        client = MagicMock()
        client.messages.create = AsyncMock(side_effect=TimeoutError("timeout"))
        model._http_client_provider.close_http_client = AsyncMock()

        with patch.object(model, "_create_async_client", return_value=client):
            with pytest.raises(TimeoutError):
                await model._generate_single({}, LlmRequest(contents=[]))

        model._http_client_provider.close_http_client.assert_awaited_once_with(client)

    @pytest.mark.asyncio
    async def test_generate_async_converts_provider_exception_to_retry_error_response(self):
        model = AnthropicModel(model_name="claude-3-5-sonnet-20241022", api_key="test-key")
        request = LlmRequest(contents=[Content(parts=[Part.from_text(text="hi")], role="user")])

        with patch.object(model, "_generate_single", side_effect=ConnectionError("offline")):
            responses = [response async for response in model.generate_async(request, stream=False)]

        assert len(responses) == 1
        assert responses[0].error_code == "API_ERROR"
        assert responses[0].custom_metadata == {"error": "offline"}


class TestAnthropicBuildUsageMetadata:
    """Tests for AnthropicModel._build_usage_metadata cache-inclusive normalization."""

    @staticmethod
    def _usage(input_tokens=100, output_tokens=50, cache_read=None, cache_creation=None):
        usage = MagicMock()
        usage.input_tokens = input_tokens
        usage.output_tokens = output_tokens
        usage.cache_read_input_tokens = cache_read
        usage.cache_creation_input_tokens = cache_creation
        return usage

    def test_cache_read_and_creation_folded_into_prompt_tokens(self):
        """prompt_token_count = input_tokens + cache_read + cache_creation."""
        usage = self._usage(input_tokens=100, output_tokens=50, cache_read=500, cache_creation=200)
        meta = AnthropicModel._build_usage_metadata(usage)
        assert meta.prompt_token_count == 100 + 500 + 200
        assert meta.candidates_token_count == 50
        assert meta.total_token_count == (100 + 500 + 200 + 50)

    def test_cache_fields_preserved_on_metadata(self):
        """cache_read_input_tokens and cache_creation_input_tokens are directly set."""
        usage = self._usage(input_tokens=100, output_tokens=50, cache_read=500, cache_creation=200)
        meta = AnthropicModel._build_usage_metadata(usage)
        assert meta.cache_read_input_tokens == 500
        assert meta.cache_creation_input_tokens == 200

    def test_none_cache_tokens_treated_as_zero(self):
        """When cache fields are None, prompt_token_count equals input_tokens only."""
        usage = self._usage(input_tokens=100, output_tokens=50, cache_read=None, cache_creation=None)
        meta = AnthropicModel._build_usage_metadata(usage)
        assert meta.prompt_token_count == 100
        assert meta.total_token_count == 150

    def test_zero_cache_tokens(self):
        """When both cache fields are 0, prompt_token_count equals input_tokens only."""
        usage = self._usage(input_tokens=200, output_tokens=30, cache_read=0, cache_creation=0)
        meta = AnthropicModel._build_usage_metadata(usage)
        assert meta.prompt_token_count == 200

    def test_only_cache_read_no_creation(self):
        """Only cache_read; cache_creation is None."""
        usage = self._usage(input_tokens=50, output_tokens=10, cache_read=300, cache_creation=None)
        meta = AnthropicModel._build_usage_metadata(usage)
        assert meta.prompt_token_count == 50 + 300
        assert meta.cache_read_input_tokens == 300
        assert meta.cache_creation_input_tokens is None


class _AnthropicRetryTestError(Exception):

    def __init__(self, status_code=None, headers=None):
        super().__init__(f"status {status_code}" if status_code is not None else "retry test")
        if status_code is not None:
            self.status_code = status_code
        if headers is not None:
            self.response = type("Resp", (), {"headers": headers})()


class TestAnthropicModelRetryHooks:

    def _model(self):
        return AnthropicModel(model_name="claude-3-5-sonnet-20241022", api_key="test-key")

    def test_x_should_retry_header_has_priority(self):
        model = self._model()
        assert model._get_model_retry_info(_AnthropicRetryTestError(400, {"x-should-retry": "true"})).should_retry is True
        assert model._get_model_retry_info(_AnthropicRetryTestError(500, {"x-should-retry": "false"})).should_retry is False

    @pytest.mark.parametrize("status_code", [408, 409, 429, 500, 503])
    def test_retryable_status_codes(self, status_code):
        assert self._model()._get_model_retry_info(_AnthropicRetryTestError(status_code)).should_retry is True

    @pytest.mark.parametrize("status_code", [400, 401, 403, 404, 499])
    def test_non_retryable_status_codes(self, status_code):
        assert self._model()._get_model_retry_info(_AnthropicRetryTestError(status_code)).should_retry is False

    def test_timeout_exception_retried(self):
        request = httpx.Request("GET", "https://example.com")
        assert self._model()._get_model_retry_info(httpx.TimeoutException("timeout", request=request)).should_retry is True

    def test_non_anthropic_exception_retried(self):
        assert self._model()._get_model_retry_info(ValueError("boom")).should_retry is True

    def test_connection_and_timeout_errors_retried(self):
        request = httpx.Request("GET", "https://example.com")
        assert self._model()._get_model_retry_info(anthropic.APIConnectionError(request=request)).should_retry is True
        assert self._model()._get_model_retry_info(anthropic.APITimeoutError(request=request)).should_retry is True

    def test_other_anthropic_error_not_retried(self):
        assert self._model()._get_model_retry_info(anthropic.AnthropicError("boom")).should_retry is False

    def test_retry_after_extracted_from_headers(self):
        info = self._model()._get_model_retry_info(_AnthropicRetryTestError(429, {"retry-after": "3"}))
        assert info.should_retry is True
        assert info.retry_after == 3.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
