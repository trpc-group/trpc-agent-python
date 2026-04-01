# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tests for Anthropic model implementation."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

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
        mock_message.usage = MagicMock(input_tokens=10, output_tokens=7)
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
        mock_final_message.usage = MagicMock(input_tokens=5, output_tokens=3)
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
