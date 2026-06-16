# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

import httpx
import openai
import pytest
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Part


class TestOpenAIModel:
    """Test suite for OpenAIModel class."""

    def test_init_basic(self):
        """Test basic initialization of OpenAIModel."""
        model = OpenAIModel(model_name="gpt-4",
                            api_key="test_key",
                            base_url="https://custom.api.com",
                            add_tools_to_prompt=True,
                            tool_prompt="xml",
                            client_args={"timeout": 30})

        assert model.name == "gpt-4"
        assert model._api_key == "test_key"
        assert model._base_url == "https://custom.api.com"
        assert model.add_tools_to_prompt is True
        assert model.tool_prompt == "xml"
        assert model.client_args == {"timeout": 30}

    def test_init_with_invalid_tool_prompt_string(self):
        """Test initialization with invalid tool prompt string raises error."""
        with pytest.raises(ValueError, match="Invalid tool_prompt string"):
            OpenAIModel(model_name="gpt-4", api_key="test_key", tool_prompt="invalid_format")

    def test_init_without_optional_params(self):
        """Test initialization without optional parameters."""
        model = OpenAIModel(model_name="gpt-4")
        assert model._api_key == ""
        assert model._base_url == ""
        assert model.add_tools_to_prompt is False
        assert model.tool_prompt == "xml"

    def test_set_api_key_and_base_url(self):
        """Test setting API key and base URL."""
        model = OpenAIModel(model_name="gpt-4", api_key="old_key", base_url="https://old.url.com")

        model.set_api_key("new_key")
        model.set_base_url("https://new.url.com")

        assert model._api_key == "new_key"
        assert model._base_url == "https://new.url.com"

    @pytest.mark.parametrize("expected_model", ["gpt", "o1", "deepseek"])
    def test_supported_models_patterns(self, expected_model):
        """Test that supported_models includes expected model patterns."""
        supported = OpenAIModel.supported_models()
        assert any(expected_model in pattern for pattern in supported)

    def test_validate_request_with_valid_request(self):
        """Test validating a valid request passes."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        content = Content(parts=[Part.from_text(text="Hello")], role="user")
        request = LlmRequest(contents=[content], config=None, tools_dict={})

        # Should not raise any exception
        model.validate_request(request)

    def test_validate_request_with_empty_contents(self):
        """Test validating request with empty contents raises ValueError."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        request = LlmRequest(contents=[], config=None, tools_dict={})

        with pytest.raises(ValueError, match="At least one content is required"):
            model.validate_request(request)

    def test_validate_request_with_empty_parts(self):
        """Test validating request with empty parts raises ValueError."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        content = Content(parts=[], role="user")
        request = LlmRequest(contents=[content], config=None, tools_dict={})

        with pytest.raises(ValueError, match="Content must have at least one part"):
            model.validate_request(request)

    def test_validate_request_with_invalid_role(self):
        """Test validating request with invalid role raises ValueError."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        content = Content(parts=[Part.from_text(text="Hello")], role="invalid_role")
        request = LlmRequest(contents=[content], config=None, tools_dict={})

        with pytest.raises(ValueError, match="Invalid content role"):
            model.validate_request(request)

    @pytest.mark.parametrize("role", ["user", "assistant", "model", "system"])
    def test_validate_request_with_valid_roles(self, role):
        """Test validating request with all valid roles."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        content = Content(parts=[Part.from_text(text="Hello")], role=role)
        request = LlmRequest(contents=[content], config=None, tools_dict={})
        # Should not raise
        model.validate_request(request)

    def test_validate_request_with_function_call(self):
        """Test validating request with function call in parts."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        part = Part.from_function_call(name="test_function", args={"param": "value"})
        content = Content(parts=[part], role="assistant")
        request = LlmRequest(contents=[content], config=None, tools_dict={})

        # Should not raise
        model.validate_request(request)

    def test_validate_request_with_function_response(self):
        """Test validating request with function response in parts."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        part = Part.from_function_response(name="test_function", response={"result": "success"})
        content = Content(parts=[part], role="user")
        request = LlmRequest(contents=[content], config=None, tools_dict={})

        # Should not raise
        model.validate_request(request)

    def test_validate_request_with_multiple_contents(self):
        """Test validating request with multiple contents."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        content1 = Content(parts=[Part.from_text(text="First message")], role="user")
        content2 = Content(parts=[Part.from_text(text="Second message")], role="assistant")
        content3 = Content(parts=[Part.from_text(text="Third message")], role="user")

        request = LlmRequest(contents=[content1, content2, content3], config=None, tools_dict={})

        # Should not raise
        model.validate_request(request)

    def test_properties_and_config(self):
        """Test model properties and config."""
        model = OpenAIModel(
            model_name="gpt-4-turbo",
            api_key="test_key",
            custom_param1="value1",
            custom_param2="value2",
        )

        assert model.name == "gpt-4-turbo"
        assert model.display_name == "OpenAIModel"
        assert model.config.get("custom_param1") == "value1"
        assert model.config.get("custom_param2") == "value2"

    def test_validate_request_with_multiple_parts_in_content(self):
        """Test validating request with multiple parts in a single content."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        parts = [Part.from_text(text="Part 1"), Part.from_text(text="Part 2"), Part.from_text(text="Part 3")]
        content = Content(parts=parts, role="user")
        request = LlmRequest(contents=[content], config=None, tools_dict={})

        # Should not raise
        model.validate_request(request)

    def test_validate_request_with_inline_data(self):
        """Test validating request with inline_data (image) in parts."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        # Create a part with inline image data
        part = Part()
        part.inline_data = Mock()
        part.inline_data.mime_type = "image/png"
        part.inline_data.data = b"fake_image_data"

        content = Content(parts=[part], role="user")
        request = LlmRequest(contents=[content], config=None, tools_dict={})

        # Should not raise
        model.validate_request(request)

    def test_validate_request_with_code_execution_result(self):
        """Test validating request with code_execution_result in parts."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        part = Part()
        part.code_execution_result = Mock()
        part.code_execution_result.outcome = Mock()
        part.code_execution_result.outcome.value = "success"
        part.code_execution_result.output = "Result output"

        content = Content(parts=[part], role="user")
        request = LlmRequest(contents=[content], config=None, tools_dict={})

        # Should not raise
        model.validate_request(request)

    def test_validate_request_with_executable_code(self):
        """Test validating request with executable_code in parts."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        part = Part()
        part.executable_code = Mock()
        part.executable_code.language = Mock()
        part.executable_code.language.value = "python"
        part.executable_code.code = "print('hello')"

        content = Content(parts=[part], role="user")
        request = LlmRequest(contents=[content], config=None, tools_dict={})

        # Should not raise
        model.validate_request(request)

    def test_validate_request_with_config(self):
        """Test validating request with GenerateContentConfig."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        config = GenerateContentConfig(max_output_tokens=100, temperature=0.7)
        content = Content(parts=[Part.from_text(text="Hello")], role="user")
        request = LlmRequest(contents=[content], config=config, tools_dict={})

        # Should not raise
        model.validate_request(request)

    def test_model_type_is_model(self):
        """Test that the model's filter type is MODEL."""
        from trpc_agent_sdk.filter import FilterType

        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        assert model._type == FilterType.MODEL

    def test_create_async_client_uses_custom_http_client_factory(self):
        """A custom http_client_factory is passed through to AsyncOpenAI."""
        shared_http_client = Mock()
        http_client_factory = Mock(return_value=shared_http_client)
        model = OpenAIModel(
            model_name="gpt-4",
            api_key="test_key",
            base_url="https://custom.api.com",
            client_args={"timeout": 30},
            http_client_factory=http_client_factory,
        )

        with patch("trpc_agent_sdk.models._openai_model.openai.AsyncOpenAI") as mock_async_openai:
            client = model._create_async_client()

        assert client is mock_async_openai.return_value
        http_client_factory.assert_called_once_with()
        mock_async_openai.assert_called_once_with(
            api_key="test_key",
            max_retries=0,
            organization="",
            base_url="https://custom.api.com",
            timeout=30,
            http_client=shared_http_client,
        )

    def test_create_async_client_default_factory_reuses_shared_http_client(self):
        """Default factory should reuse one shared httpx.AsyncClient across model calls."""
        from trpc_agent_sdk.models import _openai_model

        _openai_model._shared_http_client = None
        shared_http_client = Mock()
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        try:
            with patch("trpc_agent_sdk.models._openai_model.httpx.AsyncClient",
                       return_value=shared_http_client) as mock_httpx_client:
                with patch("trpc_agent_sdk.models._openai_model.openai.AsyncOpenAI") as mock_async_openai:
                    model._create_async_client()
                    model._create_async_client()
        finally:
            _openai_model._shared_http_client = None

        mock_httpx_client.assert_called_once_with()
        first_call_kwargs = mock_async_openai.call_args_list[0].kwargs
        second_call_kwargs = mock_async_openai.call_args_list[1].kwargs
        assert first_call_kwargs["http_client"] is shared_http_client
        assert second_call_kwargs["http_client"] is shared_http_client

    def test_create_async_client_overwrites_stale_client_args_http_client(self):
        """Factory owns http_client injection even if client_args already has one."""
        stale_http_client = Mock()
        fresh_http_client = Mock()
        http_client_factory = Mock(return_value=fresh_http_client)
        model = OpenAIModel(
            model_name="gpt-4",
            api_key="test_key",
            client_args={"http_client": stale_http_client, "timeout": 30},
            http_client_factory=http_client_factory,
        )

        with patch("trpc_agent_sdk.models._openai_model.openai.AsyncOpenAI") as mock_async_openai:
            model._create_async_client()

        assert mock_async_openai.call_args.kwargs["http_client"] is fresh_http_client
        assert mock_async_openai.call_args.kwargs["timeout"] == 30

    # ==================== Tests for generate_async method ====================

    @pytest.mark.asyncio
    async def test_generate_async_simple_text_response(self):
        """Test generate_async with a simple text response."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        content = Content(parts=[Part.from_text(text="Hello")], role="user")
        config = GenerateContentConfig(max_output_tokens=100)
        request = LlmRequest(contents=[content], config=config, tools_dict={})

        # Mock the async client
        mock_response = Mock()
        mock_response.model_dump.return_value = {
            "choices": [{
                "message": {
                    "content": "Hello, how can I help you?",
                    "role": "assistant"
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30
            }
        }

        with patch.object(model, '_create_async_client') as mock_client_factory:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            mock_client.close = AsyncMock()
            mock_client_factory.return_value = mock_client

            responses = []
            async for response in model.generate_async(request, stream=False):
                responses.append(response)

            assert len(responses) == 1
            assert responses[0].content is not None
            assert responses[0].content.parts[0].text == "Hello, how can I help you?"
            assert responses[0].usage_metadata.prompt_token_count == 10

    @pytest.mark.asyncio
    async def test_generate_async_validation_failure(self):
        """Test generate_async converts validation failures to error responses."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        request = LlmRequest(contents=[], config=None, tools_dict={})

        responses = [response async for response in model.generate_async(request, stream=False)]
        assert len(responses) == 1
        assert responses[0].error_code == "API_ERROR"
        assert "At least one content is required" in (responses[0].error_message or "")

    @pytest.mark.asyncio
    async def test_generate_async_with_config_parameters(self):
        """Test generate_async respects configuration parameters."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        content = Content(parts=[Part.from_text(text="Generate a story")], role="user")
        config = GenerateContentConfig(max_output_tokens=500, temperature=0.8, top_p=0.9, stop_sequences=["END"])
        request = LlmRequest(contents=[content], config=config, tools_dict={})

        mock_response = Mock()
        mock_response.model_dump.return_value = {
            "choices": [{
                "message": {
                    "content": "Once upon a time...",
                    "role": "assistant"
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 100,
                "total_tokens": 105
            }
        }

        with patch.object(model, '_create_async_client') as mock_client_factory:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            mock_client.close = AsyncMock()
            mock_client_factory.return_value = mock_client

            captured_api_params = None

            async def capture_create(**kwargs):
                nonlocal captured_api_params
                captured_api_params = kwargs
                return mock_response

            mock_client.chat.completions.create = capture_create

            responses = []
            async for response in model.generate_async(request, stream=False):
                responses.append(response)

            assert len(responses) == 1

    @pytest.mark.asyncio
    async def test_generate_async_with_function_call_response(self):
        """Test generate_async with tool/function call response."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        content = Content(parts=[Part.from_text(text="Call a function")], role="user")
        request = LlmRequest(contents=[content], config=None, tools_dict={})

        mock_response = Mock()
        mock_response.model_dump.return_value = {
            "choices": [{
                "message": {
                    "content":
                    None,
                    "role":
                    "assistant",
                    "tool_calls": [{
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"location": "New York"}'
                        }
                    }]
                },
                "finish_reason": "tool_calls"
            }],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 50,
                "total_tokens": 60
            }
        }

        with patch.object(model, '_create_async_client') as mock_client_factory:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            mock_client.close = AsyncMock()
            mock_client_factory.return_value = mock_client

            responses = []
            async for response in model.generate_async(request, stream=False):
                responses.append(response)

            assert len(responses) == 1
            assert responses[0].content is not None
            # Should have function call part
            function_parts = [p for p in responses[0].content.parts if p.function_call]
            assert len(function_parts) > 0

    @pytest.mark.asyncio
    async def test_generate_async_streaming_mode(self):
        """Test generate_async in streaming mode."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        content = Content(parts=[Part.from_text(text="Tell me a story")], role="user")
        request = LlmRequest(contents=[content], config=None, tools_dict={})

        # Create mock streaming response
        chunk1 = Mock()
        chunk1.model_dump.return_value = {
            "choices": [{
                "delta": {
                    "content": "Once "
                },
                "finish_reason": None,
                "index": 0
            }],
            "usage": None
        }

        chunk2 = Mock()
        chunk2.model_dump.return_value = {
            "choices": [{
                "delta": {
                    "content": "upon "
                },
                "finish_reason": None,
                "index": 0
            }],
            "usage": None
        }

        chunk3 = Mock()
        chunk3.model_dump.return_value = {
            "choices": [{
                "delta": {
                    "content": "a time..."
                },
                "finish_reason": "stop",
                "index": 0
            }],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 20,
                "total_tokens": 25
            }
        }

        async def mock_stream():
            yield chunk1
            yield chunk2
            yield chunk3

        mock_response = mock_stream()

        with patch.object(model, '_create_async_client') as mock_client_factory:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            mock_client.close = AsyncMock()
            mock_client_factory.return_value = mock_client

            responses = []
            async for response in model.generate_async(request, stream=True):
                responses.append(response)

            # Should have multiple partial responses plus final response
            assert len(responses) > 1

    @pytest.mark.asyncio
    async def test_generate_async_error_handling(self):
        """Test generate_async handles API errors gracefully."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        content = Content(parts=[Part.from_text(text="Hello")], role="user")
        request = LlmRequest(contents=[content], config=None, tools_dict={})

        with patch.object(model, '_create_async_client') as mock_client_factory:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(side_effect=Exception("API Error: Invalid API key"))
            mock_client.close = AsyncMock()
            mock_client_factory.return_value = mock_client

            responses = []
            async for response in model.generate_async(request, stream=False):
                responses.append(response)

            assert len(responses) == 1
            assert responses[0].error_code == "API_ERROR"
            assert "Invalid API key" in responses[0].error_message

    @pytest.mark.asyncio
    async def test_generate_async_with_system_instruction(self):
        """Test generate_async with system instruction in config."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        content = Content(parts=[Part.from_text(text="Hello")], role="user")
        config = GenerateContentConfig(system_instruction="You are a helpful assistant.")
        request = LlmRequest(contents=[content], config=config, tools_dict={})

        mock_response = Mock()
        mock_response.model_dump.return_value = {
            "choices": [{
                "message": {
                    "content": "Hello! How can I assist you?",
                    "role": "assistant"
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 15,
                "completion_tokens": 10,
                "total_tokens": 25
            }
        }

        with patch.object(model, '_create_async_client') as mock_client_factory:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            mock_client.close = AsyncMock()
            mock_client_factory.return_value = mock_client

            responses = []
            async for response in model.generate_async(request, stream=False):
                responses.append(response)

            assert len(responses) == 1
            assert responses[0].content.parts[0].text == "Hello! How can I assist you?"

    @pytest.mark.asyncio
    async def test_generate_async_with_multiple_contents(self):
        """Test generate_async with conversation history (multiple contents)."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        content1 = Content(parts=[Part.from_text(text="Hi")], role="user")
        content2 = Content(parts=[Part.from_text(text="Hello! How can I help?")], role="assistant")
        content3 = Content(parts=[Part.from_text(text="What's the weather?")], role="user")

        config = GenerateContentConfig(max_output_tokens=100)
        request = LlmRequest(contents=[content1, content2, content3], config=config, tools_dict={})

        mock_response = Mock()
        mock_response.model_dump.return_value = {
            "choices": [{
                "message": {
                    "content": "I don't have access to real-time weather data.",
                    "role": "assistant"
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 15,
                "total_tokens": 35
            }
        }

        with patch.object(model, '_create_async_client') as mock_client_factory:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            mock_client.close = AsyncMock()
            mock_client_factory.return_value = mock_client

            responses = []
            async for response in model.generate_async(request, stream=False):
                responses.append(response)

            assert len(responses) == 1
            assert "weather" in responses[0].content.parts[0].text.lower()

    @pytest.mark.asyncio
    async def test_generate_async_empty_response_content(self):
        """Test generate_async handles empty response content."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        content = Content(parts=[Part.from_text(text="Generate something")], role="user")
        request = LlmRequest(contents=[content], config=None, tools_dict={})

        mock_response = Mock()
        mock_response.model_dump.return_value = {
            "choices": [{
                "message": {
                    "content": None,
                    "role": "assistant"
                },
                "finish_reason": "length"
            }],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 0,
                "total_tokens": 5
            }
        }

        with patch.object(model, '_create_async_client') as mock_client_factory:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            mock_client.close = AsyncMock()
            mock_client_factory.return_value = mock_client

            responses = []
            async for response in model.generate_async(request, stream=False):
                responses.append(response)

            assert len(responses) == 1
            assert responses[0].content is not None


# ===========================================================================
# Prompt cache — request field injection
# ===========================================================================


class TestOpenAIPromptCacheRequestFields:
    """Verify prompt cache fields are added to api_params when cache config is active."""

    def _make_api_params(self) -> dict:
        """Minimal api_params skeleton similar to what OpenAIModel builds."""
        return {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}

    def _simulate_cache_injection(self, model, api_params: dict) -> None:
        """Replicate the inline cache injection logic from _generate_async_impl."""
        from trpc_agent_sdk.models._openai_model import ApiParamsKey
        cache_config = model._resolve_prompt_cache_config(None)
        if cache_config:
            if cache_config.prompt_cache_key:
                api_params[ApiParamsKey.PROMPT_CACHE_KEY] = cache_config.prompt_cache_key
            if cache_config.ttl:
                api_params[ApiParamsKey.PROMPT_CACHE_RETENTION] = cache_config.ttl

    def test_enabled_config_adds_cache_key_and_retention(self):
        """Both prompt_cache_key and prompt_cache_retention are forwarded when set."""
        from trpc_agent_sdk.configs import PromptCacheConfig
        model = OpenAIModel(
            model_name="gpt-4",
            api_key="k",
            prompt_cache_config=PromptCacheConfig(
                enabled=True,
                prompt_cache_key="weather-v1",
                ttl="24h",
            ),
        )
        api_params = self._make_api_params()
        self._simulate_cache_injection(model, api_params)
        assert api_params.get("prompt_cache_key") == "weather-v1"
        assert api_params.get("prompt_cache_retention") == "24h"

    def test_ttl_in_memory_is_forwarded(self):
        """'in_memory' is forwarded as prompt_cache_retention."""
        from trpc_agent_sdk.configs import PromptCacheConfig
        model = OpenAIModel(
            model_name="gpt-4",
            api_key="k",
            prompt_cache_config=PromptCacheConfig(enabled=True, ttl="in_memory"),
        )
        api_params = self._make_api_params()
        self._simulate_cache_injection(model, api_params)
        assert api_params.get("prompt_cache_retention") == "in_memory"

    def test_custom_ttl_is_forwarded(self):
        """TTL is provider-specific and should be forwarded without SDK validation."""
        from trpc_agent_sdk.configs import PromptCacheConfig
        model = OpenAIModel(
            model_name="gpt-4",
            api_key="k",
            prompt_cache_config=PromptCacheConfig(enabled=True, ttl="1h"),
        )
        api_params = self._make_api_params()
        self._simulate_cache_injection(model, api_params)
        assert api_params.get("prompt_cache_retention") == "1h"

    def test_disabled_config_adds_no_cache_fields(self):
        """Disabled PromptCacheConfig must not inject any cache-related keys."""
        from trpc_agent_sdk.configs import PromptCacheConfig
        from trpc_agent_sdk.models._openai_model import ApiParamsKey
        model = OpenAIModel(
            model_name="gpt-4",
            api_key="k",
            prompt_cache_config=PromptCacheConfig(enabled=False, prompt_cache_key="k", ttl="24h"),
        )
        api_params = self._make_api_params()
        self._simulate_cache_injection(model, api_params)
        assert ApiParamsKey.PROMPT_CACHE_KEY not in api_params
        assert ApiParamsKey.PROMPT_CACHE_RETENTION not in api_params

    def test_no_config_adds_no_cache_fields(self):
        """No model-level config means no cache keys are added."""
        from trpc_agent_sdk.models._openai_model import ApiParamsKey
        model = OpenAIModel(model_name="gpt-4", api_key="k")
        api_params = self._make_api_params()
        self._simulate_cache_injection(model, api_params)
        assert ApiParamsKey.PROMPT_CACHE_KEY not in api_params
        assert ApiParamsKey.PROMPT_CACHE_RETENTION not in api_params

    def test_config_without_cache_key_omits_key_field(self):
        """prompt_cache_key not in api_params when config has no prompt_cache_key."""
        from trpc_agent_sdk.configs import PromptCacheConfig
        from trpc_agent_sdk.models._openai_model import ApiParamsKey
        model = OpenAIModel(
            model_name="gpt-4",
            api_key="k",
            prompt_cache_config=PromptCacheConfig(enabled=True, ttl="24h"),
        )
        api_params = self._make_api_params()
        self._simulate_cache_injection(model, api_params)
        assert ApiParamsKey.PROMPT_CACHE_KEY not in api_params
        assert api_params.get("prompt_cache_retention") == "24h"


# ===========================================================================
# Prompt cache — usage metadata parsing
# ===========================================================================


class TestOpenAIBuildUsageMetadata:
    """Tests for OpenAIModel._build_usage_metadata cache token normalization."""

    def test_prompt_tokens_details_cached_tokens_mapped_to_cache_read(self):
        """OpenAI prompt_tokens_details.cached_tokens maps to cache_read_input_tokens."""
        usage_data = {
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "total_tokens": 1050,
            "prompt_tokens_details": {
                "cached_tokens": 800
            },
        }
        meta = OpenAIModel._build_usage_metadata(usage_data)
        assert meta.cache_read_input_tokens == 800
        assert meta.prompt_token_count == 1000
        assert meta.candidates_token_count == 50

    def test_top_level_cache_read_preferred_over_details(self):
        """If top-level cache_read_input_tokens is set, it wins over prompt_tokens_details."""
        usage_data = {
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "total_tokens": 1050,
            "cache_read_input_tokens": 600,
            "prompt_tokens_details": {
                "cached_tokens": 800
            },
        }
        meta = OpenAIModel._build_usage_metadata(usage_data)
        assert meta.cache_read_input_tokens == 600

    def test_no_cache_fields_yields_none(self):
        """When no cache fields are present, cache_read_input_tokens is None."""
        usage_data = {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        }
        meta = OpenAIModel._build_usage_metadata(usage_data)
        assert meta.cache_read_input_tokens is None
        assert meta.cache_creation_input_tokens is None

    def test_cache_creation_input_tokens_top_level(self):
        """top-level cache_creation_input_tokens (LiteLLM-compatible) is forwarded."""
        usage_data = {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "total_tokens": 110,
            "cache_creation_input_tokens": 90,
        }
        meta = OpenAIModel._build_usage_metadata(usage_data)
        assert meta.cache_creation_input_tokens == 90

    def test_empty_prompt_tokens_details_does_not_crash(self):
        """Empty prompt_tokens_details dict is handled safely."""
        usage_data = {
            "prompt_tokens": 50,
            "completion_tokens": 10,
            "total_tokens": 60,
            "prompt_tokens_details": {},
        }
        meta = OpenAIModel._build_usage_metadata(usage_data)
        assert meta.cache_read_input_tokens is None

    def test_null_prompt_tokens_details_does_not_crash(self):
        """Explicit null prompt_tokens_details is handled safely."""
        usage_data = {
            "prompt_tokens": 50,
            "completion_tokens": 10,
            "total_tokens": 60,
            "prompt_tokens_details": None,
        }
        meta = OpenAIModel._build_usage_metadata(usage_data)
        assert meta.cache_read_input_tokens is None


class _RetryTestError(Exception):

    def __init__(self, status_code=None, headers=None):
        super().__init__(f"status {status_code}" if status_code is not None else "retry test")
        if status_code is not None:
            self.status_code = status_code
        if headers is not None:
            self.response = type("Resp", (), {"headers": headers})()


class TestOpenAIModelRetryHooks:

    def _model(self):
        return OpenAIModel(model_name="gpt-4", api_key="test_key")

    def test_x_should_retry_header_has_priority(self):
        model = self._model()
        assert model._get_model_retry_info(_RetryTestError(400, {"x-should-retry": "true"})).should_retry is True
        assert model._get_model_retry_info(_RetryTestError(500, {"x-should-retry": "false"})).should_retry is False

    @pytest.mark.parametrize("status_code", [408, 409, 429, 500, 503])
    def test_retryable_status_codes(self, status_code):
        assert self._model()._get_model_retry_info(_RetryTestError(status_code)).should_retry is True

    @pytest.mark.parametrize("status_code", [400, 401, 403, 404, 499])
    def test_non_retryable_status_codes(self, status_code):
        assert self._model()._get_model_retry_info(_RetryTestError(status_code)).should_retry is False

    def test_timeout_exception_retried(self):
        request = httpx.Request("GET", "https://example.com")
        assert self._model()._get_model_retry_info(httpx.TimeoutException("timeout", request=request)).should_retry is True

    def test_openai_error_not_retried_without_status_decision(self):
        assert self._model()._get_model_retry_info(openai.OpenAIError("boom")).should_retry is False

    def test_other_exception_retried(self):
        assert self._model()._get_model_retry_info(ValueError("boom")).should_retry is True

    def test_retry_after_extracted_from_headers(self):
        info = self._model()._get_model_retry_info(_RetryTestError(429, {"retry-after": "3"}))
        assert info.should_retry is True
        assert info.retry_after == 3.0
