# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import asyncio
from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.models._openai_model import _HTTPCORE2_ATHROW_ERROR
from trpc_agent_sdk.models._openai_model import _aclose_async_iterator
from trpc_agent_sdk.models._openai_model import _aclose_openai_stream
from trpc_agent_sdk.models._openai_model import _await_drain_task
from trpc_agent_sdk.models._openai_model import _patch_stream_response_to_drain_http_body
from trpc_agent_sdk.models._openai_model import _read_http_iterator_to_end
from trpc_agent_sdk.models._openai_model import _responses_create_parameters_for_resource
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Part


def _make_fake_http_response(module_name: str, aiter_raw, aclose):
    """Build a response-like object whose type module mimics httpx or httpx2."""

    class _FakeResponse:
        def __init__(self):
            self.aiter_raw = aiter_raw
            self.aclose = aclose

    _FakeResponse.__module__ = module_name
    _FakeResponse.__name__ = "Response"
    return _FakeResponse()


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

    def test_create_async_client_uses_custom_http_client_provider(self):
        """A custom http_client_provider_factory is passed through to AsyncOpenAI."""
        shared_http_client = Mock()
        http_client_provider = Mock()
        http_client_provider.create_http_client.return_value = shared_http_client
        http_client_provider_factory = Mock(return_value=http_client_provider)
        model = OpenAIModel(
            model_name="gpt-4",
            api_key="test_key",
            base_url="https://custom.api.com",
            client_args={"timeout": 30},
            http_client_provider_factory=http_client_provider_factory,
        )

        with patch("trpc_agent_sdk.models._openai_model.openai.AsyncOpenAI") as mock_async_openai:
            client = model._create_async_client()

        assert client is mock_async_openai.return_value
        http_client_provider_factory.assert_called_once_with()
        http_client_provider.create_http_client.assert_called_once_with()
        mock_async_openai.assert_called_once_with(
            api_key="test_key",
            max_retries=0,
            organization="",
            base_url="https://custom.api.com",
            timeout=30,
            http_client=shared_http_client,
        )

    def test_create_async_client_default_factory_reuses_loop_local_http_client(self):
        """Default provider should reuse the same httpx.AsyncClient within one loop."""
        from trpc_agent_sdk.models import _httpx_client
        from trpc_agent_sdk.models import shared_http_client_provider_factory

        shared_http_client = Mock()
        shared_http_client.is_closed = False
        model = OpenAIModel(model_name="gpt-4", api_key="test_key", http_client_provider_factory=shared_http_client_provider_factory)

        try:
            _httpx_client._shared_http_clients.clear()
            with patch("trpc_agent_sdk.models._httpx_client.httpx.AsyncClient",
                       return_value=shared_http_client) as mock_httpx_client:
                with patch("trpc_agent_sdk.models._httpx_client._get_loop_key", return_value=1):
                    with patch("trpc_agent_sdk.models._openai_model.openai.AsyncOpenAI") as mock_async_openai:
                        model._create_async_client()
                        model._create_async_client()
        finally:
            _httpx_client._shared_http_clients.clear()

        mock_httpx_client.assert_called_once_with(
            limits=_httpx_client._DEFAULT_HTTP_CLIENT_LIMITS,
            timeout=_httpx_client._DEFAULT_HTTP_CLIENT_TIMEOUT,
            follow_redirects=True,
        )
        first_call_kwargs = mock_async_openai.call_args_list[0].kwargs
        second_call_kwargs = mock_async_openai.call_args_list[1].kwargs
        assert first_call_kwargs["http_client"] is shared_http_client
        assert second_call_kwargs["http_client"] is shared_http_client

    def test_create_shared_http_client_rebuilds_closed_client(self):
        """Closed cached clients should be replaced on the next factory call."""
        from trpc_agent_sdk.models import _httpx_client

        closed_client = Mock()
        closed_client.is_closed = True
        fresh_client = Mock()
        fresh_client.is_closed = False

        try:
            _httpx_client._shared_http_clients.clear()
            client_key = (1234, 1)
            _httpx_client._shared_http_clients[client_key] = closed_client
            with patch("trpc_agent_sdk.models._httpx_client._get_client_key", return_value=client_key):
                with patch("trpc_agent_sdk.models._httpx_client.httpx.AsyncClient",
                           return_value=fresh_client) as mock_httpx_client:
                    assert _httpx_client._create_shared_http_client() is fresh_client
        finally:
            _httpx_client._shared_http_clients.clear()

        mock_httpx_client.assert_called_once()

    def test_create_shared_http_client_does_not_reuse_across_loop_keys(self):
        """Different event loops should get different default httpx clients."""
        from trpc_agent_sdk.models import _httpx_client

        first_client = Mock()
        first_client.is_closed = False
        second_client = Mock()
        second_client.is_closed = False

        try:
            _httpx_client._shared_http_clients.clear()
            with patch("trpc_agent_sdk.models._httpx_client.httpx.AsyncClient",
                       side_effect=[first_client, second_client]) as mock_httpx_client:
                with patch("trpc_agent_sdk.models._httpx_client._get_client_key", side_effect=[(1234, 1), (1234, 2)]):
                    assert _httpx_client._create_shared_http_client() is first_client
                    assert _httpx_client._create_shared_http_client() is second_client
        finally:
            _httpx_client._shared_http_clients.clear()

        assert mock_httpx_client.call_count == 2

    def test_create_shared_http_client_does_not_reuse_across_process_keys(self):
        """Different process keys should get different default httpx clients."""
        from trpc_agent_sdk.models import _httpx_client

        parent_client = Mock()
        parent_client.is_closed = False
        child_client = Mock()
        child_client.is_closed = False

        try:
            _httpx_client._shared_http_clients.clear()
            with patch("trpc_agent_sdk.models._httpx_client.httpx.AsyncClient",
                       side_effect=[parent_client, child_client]) as mock_httpx_client:
                with patch("trpc_agent_sdk.models._httpx_client._get_client_key",
                           side_effect=[(1234, 1), (5678, 1)]):
                    assert _httpx_client._create_shared_http_client() is parent_client
                    assert _httpx_client._create_shared_http_client() is child_client
        finally:
            _httpx_client._shared_http_clients.clear()

        assert mock_httpx_client.call_count == 2

    def test_reset_shared_http_clients_after_fork_clears_cache_and_rebuilds_lock(self):
        """Fork child reset should drop inherited clients and replace inherited locks."""
        from trpc_agent_sdk.models import _httpx_client

        inherited_client = Mock()
        old_lock = _httpx_client._shared_http_clients_lock

        try:
            _httpx_client._shared_http_clients[(1234, 1)] = inherited_client

            _httpx_client._reset_shared_http_clients_after_fork()

            assert _httpx_client._shared_http_clients == {}
            assert _httpx_client._shared_http_clients_lock is not old_lock
        finally:
            _httpx_client._shared_http_clients.clear()

    def test_create_async_client_overwrites_stale_client_args_http_client(self):
        """Provider owns http_client injection even if client_args already has one."""
        stale_http_client = Mock()
        fresh_http_client = Mock()
        http_client_provider = Mock()
        http_client_provider.create_http_client.return_value = fresh_http_client
        http_client_provider_factory = Mock(return_value=http_client_provider)
        model = OpenAIModel(
            model_name="gpt-4",
            api_key="test_key",
            client_args={"http_client": stale_http_client, "timeout": 30},
            http_client_provider_factory=http_client_provider_factory,
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
        """Test generate_async returns an error response on invalid request."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")

        # Empty contents
        request = LlmRequest(contents=[], config=None, tools_dict={})

        responses = []
        async for response in model.generate_async(request, stream=False):
            responses.append(response)

        assert len(responses) == 1
        assert responses[0].error_code == "API_ERROR"
        assert "At least one content is required" in responses[0].error_message

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
    async def test_generate_async_streaming_closes_openai_stream(self):
        """Chat Completions streaming must aclose the SDK stream after use."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")
        content = Content(parts=[Part.from_text(text="Tell me a story")], role="user")
        request = LlmRequest(contents=[content], config=None, tools_dict={})
        chunk = Mock()
        chunk.model_dump.return_value = {
            "choices": [{
                "delta": {"content": "Hi"},
                "finish_reason": "stop",
                "index": 0,
            }],
            "usage": None,
        }

        class _CloseableStream:
            def __init__(self):
                self.aclose = AsyncMock()

            async def __aiter__(self):
                yield chunk

        stream = _CloseableStream()
        with patch.object(model, "_create_async_client") as mock_client_factory:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=stream)
            mock_client.close = AsyncMock()
            mock_client_factory.return_value = mock_client
            async for _ in model.generate_async(request, stream=True):
                pass
        stream.aclose.assert_awaited()

    @pytest.mark.asyncio
    async def test_patch_skips_httpx_response(self):
        """openai 1.x/2.x httpx responses must keep the original close path."""
        inner_finished = False

        async def paused_body(chunk_size=None):
            nonlocal inner_finished
            yield b"data: hello\n\n"
            yield b"data: [DONE]\n\n"
            yield b"0\r\n\r\n"
            inner_finished = True

        orig_aiter_raw = paused_body
        closed = False

        async def orig_aclose():
            nonlocal closed
            closed = True

        class _FakeStream:
            def __init__(self):
                self.response = _make_fake_http_response("httpx._models", paused_body, orig_aclose)

        stream = _FakeStream()
        _patch_stream_response_to_drain_http_body(stream)
        assert stream.response.aiter_raw is orig_aiter_raw
        await stream.response.aclose()
        assert closed is True
        assert inner_finished is False

    @pytest.mark.asyncio
    async def test_patch_stream_response_drains_paused_http_body(self):
        """httpx2 response.aclose() after SSE [DONE] must exhaust leftover HTTP bytes."""
        inner_finished = False

        async def paused_body(chunk_size=None):
            nonlocal inner_finished
            yield b"data: hello\n\n"
            yield b"data: [DONE]\n\n"
            yield b"0\r\n\r\n"
            inner_finished = True

        closed = False

        async def orig_aclose():
            nonlocal closed
            closed = True

        class _FakeStream:
            def __init__(self):
                self.response = _make_fake_http_response("httpx2._models", paused_body, orig_aclose)

        stream = _FakeStream()
        _patch_stream_response_to_drain_http_body(stream)
        assert stream.response.aiter_raw is not paused_body

        agen = stream.response.aiter_raw()
        assert await agen.__anext__() == b"data: hello\n\n"
        assert await agen.__anext__() == b"data: [DONE]\n\n"
        await stream.response.aclose()
        await agen.aclose()
        assert inner_finished is True
        assert closed is True

    @pytest.mark.asyncio
    async def test_patch_closes_connection_before_retrying_hung_drain(self):
        """If leftover body hangs, close TCP first so the iterator can finish."""
        inner_finished = False
        connection_closed = asyncio.Event()
        aclose_before_finish = []

        async def hung_body(chunk_size=None):
            nonlocal inner_finished
            yield b"data: hello\n\n"
            yield b"data: [DONE]\n\n"
            await connection_closed.wait()
            yield b"0\r\n\r\n"
            inner_finished = True

        async def orig_aclose():
            aclose_before_finish.append(inner_finished)
            connection_closed.set()

        class _FakeStream:
            def __init__(self):
                self.response = _make_fake_http_response("httpx2._models", hung_body, orig_aclose)

        stream = _FakeStream()
        _patch_stream_response_to_drain_http_body(stream)
        agen = stream.response.aiter_raw()
        assert await agen.__anext__() == b"data: hello\n\n"
        assert await agen.__anext__() == b"data: [DONE]\n\n"
        with patch("trpc_agent_sdk.models._openai_model._HTTP_BODY_DRAIN_TIMEOUT_S", 0.05):
            await stream.response.aclose()
        await agen.aclose()
        assert aclose_before_finish == [False]
        assert inner_finished is True

    @pytest.mark.asyncio
    async def test_patch_concurrent_aclose_and_generator_exit_drains_once(self):
        """response.aclose() and aiter_raw GeneratorExit must share one drain."""
        inner_finished = False
        connection_closed = asyncio.Event()
        drain_calls = {"n": 0}

        async def hung_body(chunk_size=None):
            nonlocal inner_finished
            yield b"data: hello\n\n"
            yield b"data: [DONE]\n\n"
            await connection_closed.wait()
            yield b"0\r\n\r\n"
            inner_finished = True

        async def orig_aclose():
            connection_closed.set()

        class _FakeStream:
            def __init__(self):
                self.response = _make_fake_http_response("httpx2._models", hung_body, orig_aclose)

        async def counting_drain(iterator):
            drain_calls["n"] += 1
            await _read_http_iterator_to_end(iterator)

        stream = _FakeStream()
        _patch_stream_response_to_drain_http_body(stream)
        agen = stream.response.aiter_raw()
        assert await agen.__anext__() == b"data: hello\n\n"
        assert await agen.__anext__() == b"data: [DONE]\n\n"
        with patch("trpc_agent_sdk.models._openai_model._read_http_iterator_to_end", counting_drain), \
             patch("trpc_agent_sdk.models._openai_model._HTTP_BODY_DRAIN_TIMEOUT_S", 0.05):
            await asyncio.gather(stream.response.aclose(), agen.aclose())
        assert drain_calls["n"] == 1
        assert inner_finished is True

    @pytest.mark.asyncio
    async def test_await_drain_task_reuses_completed_task(self):
        drain_calls = {"n": 0}

        async def body():
            yield b"chunk"

        async def counting_drain(iterator):
            drain_calls["n"] += 1
            await _read_http_iterator_to_end(iterator)

        iterator = body().__aiter__()
        held = {"iterator": iterator, "drain_task": None}
        with patch("trpc_agent_sdk.models._openai_model._read_http_iterator_to_end", counting_drain):
            assert await _await_drain_task(held) is True
            assert await _await_drain_task(held) is True
        assert drain_calls["n"] == 1

    async def test_generate_async_streaming_preserves_text_with_native_tool_call(self):
        """Final streaming response keeps regular text alongside native tool calls."""
        model = OpenAIModel(model_name="gpt-4", api_key="test_key")
        content = Content(parts=[Part.from_text(text="What's the weather?")], role="user")
        request = LlmRequest(contents=[content], config=None, tools_dict={})
        text_chunk = Mock()
        text_chunk.model_dump.return_value = {
            "choices": [{
                "delta": {
                    "content": "I'll check the weather first."
                },
                "finish_reason": None,
                "index": 0,
            }],
            "usage": None,
        }
        tool_chunk = Mock()
        tool_chunk.model_dump.return_value = {
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "id": "call_weather",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "Beijing"}',
                        },
                    }]
                },
                "finish_reason": "tool_calls",
                "index": 0,
            }],
            "usage": None,
        }
        async def mock_stream():
            yield text_chunk
            yield tool_chunk
        with patch.object(model, '_create_async_client') as mock_client_factory:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_stream())
            mock_client.close = AsyncMock()
            mock_client_factory.return_value = mock_client
            responses = []
            async for response in model.generate_async(request, stream=True):
                responses.append(response)
        final_response = responses[-1]
        assert final_response.partial is False
        assert final_response.content is not None
        text_parts = [part.text for part in final_response.content.parts if part.text]
        function_parts = [part.function_call for part in final_response.content.parts if part.function_call]
        assert text_parts == ["I'll check the weather first."]
        assert len(function_parts) == 1
        assert function_parts[0].name == "get_weather"
        assert function_parts[0].args == {"city": "Beijing"}

    @pytest.mark.asyncio
    async def test_generate_async_streaming_suppresses_tool_prompt_markup_but_keeps_visible_text(self):
        """Provider tool-prompt markup is hidden from final text while visible text is preserved."""
        model = OpenAIModel(model_name="hy3-preview", api_key="test_key", add_tools_to_prompt=True)
        content = Content(parts=[Part.from_text(text="What's the weather?")], role="user")
        request = LlmRequest(contents=[content], config=None, tools_dict={})
        tool_prompt_chunk = Mock()
        tool_prompt_chunk.model_dump.return_value = {
            "choices": [{
                "delta": {
                    "content": ("I'll check the weather first. "
                                "<tool_call>get_weather<tool_sep>"
                                "<arg_key>city</arg_key><arg_value>Beijing</arg_value>"
                                "</tool_call>")
                },
                "finish_reason": "stop",
                "index": 0,
            }],
            "usage": None,
        }
        async def mock_stream():
            yield tool_prompt_chunk
        with patch.object(model, '_create_async_client') as mock_client_factory:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_stream())
            mock_client.close = AsyncMock()
            mock_client_factory.return_value = mock_client
            responses = []
            async for response in model.generate_async(request, stream=True):
                responses.append(response)
        final_response = responses[-1]
        assert final_response.partial is False
        assert final_response.content is not None
        text = "".join(part.text for part in final_response.content.parts if part.text)
        function_parts = [part.function_call for part in final_response.content.parts if part.function_call]
        assert text == "I'll check the weather first. "
        assert "<tool_call>" not in text
        assert len(function_parts) == 1
        assert function_parts[0].name == "get_weather"
        assert function_parts[0].args == {"city": "Beijing"}

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


def _httpx2_stream(aiter_raw, aclose):
    class _FakeStream:
        def __init__(self):
            self.response = _make_fake_http_response("httpx2._models", aiter_raw, aclose)

    return _FakeStream()


def _httpx2_stream_aclose_on_aiter_end(body, orig_aclose):
    """Mimic httpx2 Response.aiter_raw(), which calls self.aclose() in finally."""

    class _FakeResponse:
        def __init__(self):
            self.aclose = orig_aclose

        async def aiter_raw(self, chunk_size=None):
            try:
                async for chunk in body(chunk_size):
                    yield chunk
            finally:
                await self.aclose()

    _FakeResponse.__module__ = "httpx2._models"
    _FakeResponse.__name__ = "Response"

    class _FakeStream:
        def __init__(self):
            self.response = _FakeResponse()

    return _FakeStream()


class TestOpenAIHttpBodyDrainExceptions:
    """Exception and edge paths for httpx2 SSE body drain helpers."""

    async def test_await_drain_task_missing_iterator_is_success(self):
        assert await _await_drain_task({"iterator": None, "drain_task": None}) is True

    async def test_await_drain_task_cancelled_task_is_failure(self):
        async def hanging():
            await asyncio.Event().wait()
            yield b"x"

        iterator = hanging().__aiter__()
        task = asyncio.create_task(iterator.__anext__())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        held = {"iterator": iterator, "drain_task": task}
        assert await _await_drain_task(held) is False

    async def test_await_drain_task_iterator_error_is_failure(self):
        class _Boom:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise RuntimeError("truncated body")

        assert await _await_drain_task({"iterator": _Boom(), "drain_task": None}) is False

    async def test_aclose_async_iterator_without_aclose_is_noop(self):
        await _aclose_async_iterator(object())

    async def test_aclose_async_iterator_ignores_httpcore2_athrow(self):
        class _HttpcoreBoom:
            async def aclose(self):
                raise RuntimeError(_HTTPCORE2_ATHROW_ERROR)

        await _aclose_async_iterator(_HttpcoreBoom())

    async def test_aclose_async_iterator_reraises_other_runtime_error(self):
        class _Boom:
            async def aclose(self):
                raise RuntimeError("unrelated close failure")

        with pytest.raises(RuntimeError, match="unrelated close failure"):
            await _aclose_async_iterator(_Boom())

    async def test_aclose_async_iterator_swallows_generic_error(self):
        class _Boom:
            async def aclose(self):
                raise ValueError("close failed")

        await _aclose_async_iterator(_Boom())

    async def test_patch_skips_none_stream_and_missing_response(self):
        _patch_stream_response_to_drain_http_body(None)
        _patch_stream_response_to_drain_http_body(object())

    async def test_patch_is_idempotent(self):
        async def body(chunk_size=None):
            yield b"data: hello\n\n"

        async def orig_aclose():
            return None

        stream = _httpx2_stream(body, orig_aclose)
        _patch_stream_response_to_drain_http_body(stream)
        wrapped = stream.response.aiter_raw
        _patch_stream_response_to_drain_http_body(stream)
        assert stream.response.aiter_raw is wrapped

    async def test_patch_swallows_immutable_response(self):
        async def body(chunk_size=None):
            yield b"data: hello\n\n"

        async def orig_aclose():
            return None

        class _FrozenResponse:
            def __init__(self):
                object.__setattr__(self, "aiter_raw", body)
                object.__setattr__(self, "aclose", orig_aclose)

            def __setattr__(self, name, value):
                raise AttributeError(name)

        _FrozenResponse.__module__ = "httpx2._models"

        class _FakeStream:
            def __init__(self):
                self.response = _FrozenResponse()

        stream = _FakeStream()
        _patch_stream_response_to_drain_http_body(stream)
        assert stream.response.aiter_raw is body

    async def test_exhausted_aiter_raw_does_not_close_http_response(self):
        response_closed = False

        async def body(chunk_size=None):
            yield b"data: hello\n\n"

        async def orig_aclose():
            nonlocal response_closed
            response_closed = True

        stream = _httpx2_stream(body, orig_aclose)
        _patch_stream_response_to_drain_http_body(stream)
        agen = stream.response.aiter_raw()
        assert await agen.__anext__() == b"data: hello\n\n"
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()
        assert response_closed is False

    async def test_second_aclose_does_not_reclose_tcp(self):
        orig_calls = []

        async def body(chunk_size=None):
            yield b"data: hello\n\n"
            yield b"data: [DONE]\n\n"
            yield b"0\r\n\r\n"

        async def orig_aclose():
            orig_calls.append(1)

        stream = _httpx2_stream(body, orig_aclose)
        _patch_stream_response_to_drain_http_body(stream)
        agen = stream.response.aiter_raw()
        assert await agen.__anext__() == b"data: hello\n\n"
        assert await agen.__anext__() == b"data: [DONE]\n\n"
        await stream.response.aclose()
        await stream.response.aclose()
        await agen.aclose()
        assert orig_calls == [1]

    async def test_patch_drain_does_not_deadlock_when_inner_aiter_closes_response(self):
        """httpx2 aiter_raw finally calls response.aclose(); drain must not join itself."""
        inner_finished = False

        async def paused_body(chunk_size=None):
            nonlocal inner_finished
            yield b"data: hello\n\n"
            yield b"data: [DONE]\n\n"
            yield b"0\r\n\r\n"
            inner_finished = True

        closed = False

        async def orig_aclose():
            nonlocal closed
            closed = True

        stream = _httpx2_stream_aclose_on_aiter_end(paused_body, orig_aclose)
        _patch_stream_response_to_drain_http_body(stream)
        agen = stream.response.aiter_raw()
        assert await agen.__anext__() == b"data: hello\n\n"
        assert await agen.__anext__() == b"data: [DONE]\n\n"
        await asyncio.wait_for(stream.response.aclose(), timeout=1.0)
        await asyncio.wait_for(agen.aclose(), timeout=1.0)
        assert inner_finished is True
        assert closed is True

    async def test_patch_hung_drain_does_not_deadlock_when_inner_aiter_closes_response(self):
        """TCP close unblocks leftover bytes; inner aiter_raw.aclose must not deadlock."""
        inner_finished = False
        connection_closed = asyncio.Event()

        async def hung_body(chunk_size=None):
            nonlocal inner_finished
            yield b"data: hello\n\n"
            yield b"data: [DONE]\n\n"
            await connection_closed.wait()
            yield b"0\r\n\r\n"
            inner_finished = True

        async def orig_aclose():
            connection_closed.set()

        stream = _httpx2_stream_aclose_on_aiter_end(hung_body, orig_aclose)
        _patch_stream_response_to_drain_http_body(stream)
        agen = stream.response.aiter_raw()
        assert await agen.__anext__() == b"data: hello\n\n"
        assert await agen.__anext__() == b"data: [DONE]\n\n"
        with patch("trpc_agent_sdk.models._openai_model._HTTP_BODY_DRAIN_TIMEOUT_S", 0.05):
            await asyncio.wait_for(stream.response.aclose(), timeout=1.0)
        await asyncio.wait_for(agen.aclose(), timeout=1.0)
        assert inner_finished is True

    async def test_follower_aclose_skips_orig_aclose_after_leader_tcp_close(self):
        orig_calls = []
        connection_closed = asyncio.Event()
        tcp_closed = asyncio.Event()

        async def hung_body(chunk_size=None):
            yield b"data: hello\n\n"
            yield b"data: [DONE]\n\n"
            await connection_closed.wait()
            yield b"0\r\n\r\n"

        async def orig_aclose():
            orig_calls.append(1)
            connection_closed.set()
            tcp_closed.set()

        stream = _httpx2_stream(hung_body, orig_aclose)
        _patch_stream_response_to_drain_http_body(stream)
        agen = stream.response.aiter_raw()
        assert await agen.__anext__() == b"data: hello\n\n"
        assert await agen.__anext__() == b"data: [DONE]\n\n"
        with patch("trpc_agent_sdk.models._openai_model._HTTP_BODY_DRAIN_TIMEOUT_S", 0.05):
            gen_close = asyncio.create_task(agen.aclose())
            await asyncio.wait_for(tcp_closed.wait(), timeout=1.0)
            await stream.response.aclose()
            await gen_close
        assert orig_calls == [1]

    async def test_follower_aclose_joins_failed_drain(self):
        orig_calls = []

        async def boom_body(chunk_size=None):
            yield b"data: hello\n\n"
            raise RuntimeError("truncated")

        async def orig_aclose():
            orig_calls.append(1)

        stream = _httpx2_stream(boom_body, orig_aclose)
        _patch_stream_response_to_drain_http_body(stream)
        agen = stream.response.aiter_raw()
        assert await agen.__anext__() == b"data: hello\n\n"
        await asyncio.gather(stream.response.aclose(), stream.response.aclose())
        assert orig_calls

    async def test_hung_drain_survives_orig_aclose_error(self):
        inner_finished = False
        connection_closed = asyncio.Event()

        async def hung_body(chunk_size=None):
            nonlocal inner_finished
            yield b"data: hello\n\n"
            yield b"data: [DONE]\n\n"
            await connection_closed.wait()
            yield b"0\r\n\r\n"
            inner_finished = True

        async def orig_aclose():
            connection_closed.set()
            raise RuntimeError("tcp close failed")

        stream = _httpx2_stream(hung_body, orig_aclose)
        _patch_stream_response_to_drain_http_body(stream)
        agen = stream.response.aiter_raw()
        assert await agen.__anext__() == b"data: hello\n\n"
        assert await agen.__anext__() == b"data: [DONE]\n\n"
        with patch("trpc_agent_sdk.models._openai_model._HTTP_BODY_DRAIN_TIMEOUT_S", 0.05):
            await stream.response.aclose()
        await agen.aclose()
        assert inner_finished is True

    async def test_aclose_openai_stream_none_and_missing_closer(self):
        await _aclose_openai_stream(None)
        await _aclose_openai_stream(object())

    async def test_aclose_openai_stream_sync_close(self):
        class _SyncClose:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        stream = _SyncClose()
        await _aclose_openai_stream(stream)
        assert stream.closed is True

    async def test_aclose_openai_stream_ignores_httpcore2_athrow(self):
        class _HttpcoreBoom:
            async def aclose(self):
                raise RuntimeError(_HTTPCORE2_ATHROW_ERROR)

        await _aclose_openai_stream(_HttpcoreBoom())

    async def test_aclose_openai_stream_reraises_other_runtime_error(self):
        class _Boom:
            async def aclose(self):
                raise RuntimeError("stream close failed")

        with pytest.raises(RuntimeError, match="stream close failed"):
            await _aclose_openai_stream(_Boom())

    def test_responses_create_parameters_wraps_signature_errors(self):
        class _BadResource:
            create = 123

        _responses_create_parameters_for_resource.cache_clear()
        with pytest.raises(ValueError, match="Unable to determine Responses logprobs support"):
            _responses_create_parameters_for_resource(_BadResource)
