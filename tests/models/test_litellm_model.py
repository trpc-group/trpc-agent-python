# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tests for LiteLLMModel."""

from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from trpc_agent_sdk.models import LiteLLMModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import CHOICES
from trpc_agent_sdk.models import USAGE
from trpc_agent_sdk.models import DELTA
from trpc_agent_sdk.models import CONTENT
from trpc_agent_sdk.models import FINISH_REASON
from trpc_agent_sdk.models import MESSAGE
from trpc_agent_sdk.models import TOOL_CALLS
from trpc_agent_sdk.models._litellm_model import _build_response_format_for_litellm
from trpc_agent_sdk.models._litellm_model import _is_litellm_gemini_model
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Part


class TestLiteLLMModelInit:
    def test_init_valid_provider_model(self):
        model = LiteLLMModel(model_name="openai/gpt-4", api_key="key", base_url="https://api.example.com")
        assert model._model_name == "openai/gpt-4"
        assert model._api_key == "key"
        assert model._base_url == "https://api.example.com"
        assert model.add_tools_to_prompt is False
        assert model.tool_prompt == "xml"

    def test_init_invalid_no_slash_raises(self):
        with pytest.raises(ValueError, match="provider/model format"):
            LiteLLMModel(model_name="gpt-4")

    def test_supported_models_includes_providers(self):
        supported = LiteLLMModel.supported_models()
        assert any("openai" in p for p in supported)
        assert any("anthropic" in p for p in supported)
        assert any("gemini" in p for p in supported)
        assert any("vertex_ai" in p for p in supported)

    def test_ensure_litellm_imported_raises_when_litellm_not_installed(self):
        model = LiteLLMModel(model_name="openai/gpt-4")
        with patch("importlib.util.find_spec", return_value=None):
            with pytest.raises(ImportError, match="litellm"):
                model._ensure_litellm_imported()


class TestGetMessageContent:
    def test_content_string(self):
        model = LiteLLMModel(model_name="openai/gpt-4")
        msg = {"content": "hello"}
        assert model._get_message_content(msg) == "hello"

    def test_content_list_blocks(self):
        model = LiteLLMModel(model_name="openai/gpt-4")
        msg = {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
        assert model._get_message_content(msg) == "a b"

    def test_content_none_or_missing(self):
        model = LiteLLMModel(model_name="openai/gpt-4")
        assert model._get_message_content({}) == ""
        assert model._get_message_content({"content": None}) == ""

    def test_content_list_block_with_text_key(self):
        model = LiteLLMModel(model_name="openai/gpt-4")
        msg = {"content": [{"text": "only"}]}
        assert model._get_message_content(msg) == "only"


class TestCreateResponseWithContent:
    def test_no_choices_returns_usage_and_error_code(self):
        model = LiteLLMModel(model_name="openai/gpt-4")
        response_dict = {
            CHOICES: [],
            USAGE: {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }
        r = model._create_response_with_content(response_dict, partial=False)
        assert r.content is None
        assert r.error_code == "NO_CHOICES"
        assert r.error_message == "No choices in response"
        assert r.usage_metadata is not None
        assert r.usage_metadata.prompt_token_count == 1
        assert r.usage_metadata.candidates_token_count == 2

    def test_no_message_returns_usage_and_error_code(self):
        model = LiteLLMModel(model_name="openai/gpt-4")
        response_dict = {
            CHOICES: [{}],
            USAGE: {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
        }
        r = model._create_response_with_content(response_dict, partial=False)
        assert r.content is None
        assert r.error_code == "NO_MESSAGE"
        assert r.usage_metadata is not None
        assert r.usage_metadata.prompt_token_count == 5

    def test_normal_text_and_usage_no_error_code_when_stop(self):
        model = LiteLLMModel(model_name="openai/gpt-4")
        response_dict = {
            CHOICES: [{
                MESSAGE: {"content": "Hi there", "role": "assistant"},
                FINISH_REASON: "stop",
            }],
            USAGE: {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }
        r = model._create_response_with_content(response_dict, partial=False)
        assert r.error_code is None
        assert r.content is not None
        assert len(r.content.parts) == 1
        assert r.content.parts[0].text == "Hi there"
        assert getattr(r.content.parts[0], "thought", None) is False
        assert r.usage_metadata.prompt_token_count == 10

    def test_finish_reason_not_stop_sets_error_code(self):
        model = LiteLLMModel(model_name="openai/gpt-4")
        response_dict = {
            CHOICES: [{
                MESSAGE: {"content": "x", "role": "assistant"},
                FINISH_REASON: "length",
            }],
            USAGE: {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        r = model._create_response_with_content(response_dict, partial=False)
        assert r.error_code == "length"
        assert r.content is not None
        assert r.content.parts[0].text == "x"

    def test_tool_calls_from_message(self):
        model = LiteLLMModel(model_name="openai/gpt-4")
        response_dict = {
            CHOICES: [{
                MESSAGE: {
                    "content": None,
                    "role": "assistant",
                    TOOL_CALLS: [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city": "NYC"}'},
                    }],
                },
                FINISH_REASON: "tool_calls",
            }],
            USAGE: {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        }
        r = model._create_response_with_content(response_dict, partial=False)
        assert r.content is not None
        fn_parts = [p for p in r.content.parts if getattr(p, "function_call", None)]
        assert len(fn_parts) == 1
        assert fn_parts[0].function_call.name == "get_weather"
        assert fn_parts[0].function_call.args == {"city": "NYC"}
        assert fn_parts[0].function_call.id == "call_1"

    def test_first_choice_none_uses_empty_dict(self):
        model = LiteLLMModel(model_name="openai/gpt-4")
        response_dict = {
            CHOICES: [None],
            USAGE: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        r = model._create_response_with_content(response_dict, partial=False)
        assert r.error_code == "NO_MESSAGE"
        assert r.usage_metadata is not None

    def test_empty_parts_yields_single_empty_text_part_with_thought_false(self):
        model = LiteLLMModel(model_name="openai/gpt-4")
        response_dict = {
            CHOICES: [{
                MESSAGE: {"content": "", "role": "assistant"},
                FINISH_REASON: "stop",
            }],
            USAGE: {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
        }
        r = model._create_response_with_content(response_dict, partial=False)
        assert r.content is not None
        assert len(r.content.parts) == 1
        assert r.content.parts[0].text == ""
        assert getattr(r.content.parts[0], "thought", None) is False


class TestBuildResponseFormatForLitellm:
    def test_gemini_model_returns_response_schema(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        out = _build_response_format_for_litellm(schema, "gemini/gemini-1.5-pro")
        assert out is not None
        assert out["type"] == "json_object"
        assert "response_schema" in out
        assert out["response_schema"] == schema

    def test_vertex_gemini_returns_response_schema(self):
        schema = {"type": "object", "properties": {}}
        out = _build_response_format_for_litellm(schema, "vertex_ai/gemini-2.0-flash")
        assert out is not None
        assert out["type"] == "json_object"
        assert "response_schema" in out

    def test_openai_compatible_returns_json_schema_with_additional_properties_false(self):
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        out = _build_response_format_for_litellm(schema, "openai/gpt-4")
        assert out is not None
        assert out["type"] == "json_schema"
        assert "json_schema" in out
        assert out["json_schema"]["schema"].get("additionalProperties") is False

    def test_is_litellm_gemini_model(self):
        assert _is_litellm_gemini_model("gemini/gemini-1.5-pro") is True
        assert _is_litellm_gemini_model("vertex_ai/gemini-2.0-flash") is True
        assert _is_litellm_gemini_model("openai/gpt-4") is False


class TestLogUnsupportedConfigOptions:
    @pytest.mark.filterwarnings("ignore:.*is not a valid.*:UserWarning")
    def test_logs_warning_when_unsupported_set(self):
        model = LiteLLMModel(model_name="openai/gpt-4")
        config = GenerateContentConfig(
            top_k=1,
            safety_settings=[{"category": "HARM", "threshold": "BLOCK_MEDIUM"}],
        )
        with patch("trpc_agent_sdk.models._litellm_model.logger") as mock_logger:
            model._log_unsupported_config_options(config)
        mock_logger.warning.assert_called_once()
        call_msg = mock_logger.warning.call_args[0][0]
        assert "LiteLLM" in call_msg
        assert "top_k" in call_msg
        assert "safety_settings" in call_msg

    def test_no_warning_when_none_of_unsupported_set(self):
        model = LiteLLMModel(model_name="openai/gpt-4")
        config = GenerateContentConfig(max_output_tokens=100, temperature=0.5)
        with patch("trpc_agent_sdk.models._litellm_model.logger") as mock_logger:
            model._log_unsupported_config_options(config)
        mock_logger.warning.assert_not_called()


class TestGenerateAsyncNonStream:
    @pytest.mark.asyncio
    async def test_generate_async_non_stream_success(self):
        model = LiteLLMModel(model_name="openai/gpt-4", api_key="key")
        content = Content(parts=[Part.from_text(text="Hello")], role="user")
        request = LlmRequest(contents=[content], config=GenerateContentConfig(max_output_tokens=10), tools_dict={})

        mock_response = Mock()
        mock_response.model_dump.return_value = {
            CHOICES: [{
                MESSAGE: {"content": "Hi!", "role": "assistant"},
                FINISH_REASON: "stop",
            }],
            USAGE: {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        }

        async def fake_acompletion(**kwargs):
            return mock_response

        import builtins
        real_import = builtins.__import__
        mock_litellm = Mock()
        mock_litellm.acompletion = AsyncMock(side_effect=fake_acompletion)
        def fake_import(name, *a, **k):
            if name == "litellm":
                return mock_litellm
            return real_import(name, *a, **k)
        with patch.object(model, "_ensure_litellm_imported"):
            with patch("builtins.__import__", side_effect=fake_import):
                responses = []
                async for r in model.generate_async(request, stream=False):
                    responses.append(r)
                assert len(responses) == 1
                assert responses[0].content is not None
                assert responses[0].content.parts[0].text == "Hi!"
                assert responses[0].usage_metadata.prompt_token_count == 2
                assert responses[0].error_code is None

    @pytest.mark.asyncio
    async def test_generate_async_non_stream_api_error(self):
        model = LiteLLMModel(model_name="openai/gpt-4")
        content = Content(parts=[Part.from_text(text="Hi")], role="user")
        request = LlmRequest(contents=[content], config=None, tools_dict={})

        import builtins
        real_import = builtins.__import__
        mock_litellm = Mock()
        mock_litellm.acompletion = AsyncMock(side_effect=Exception("Connection refused"))
        def fake_import(name, *a, **k):
            if name == "litellm":
                return mock_litellm
            return real_import(name, *a, **k)
        with patch.object(model, "_ensure_litellm_imported"):
            with patch("builtins.__import__", side_effect=fake_import):

                responses = []
                async for r in model.generate_async(request, stream=False):
                    responses.append(r)
                assert len(responses) == 1
                assert responses[0].error_code == "API_ERROR"
                assert "Connection refused" in (responses[0].error_message or "")

    @pytest.mark.asyncio
    async def test_generate_async_passes_response_format_for_openai_model(self):
        import builtins
        real_import = builtins.__import__
        captured_kwargs = {}
        async def capture_acompletion(**kwargs):
            captured_kwargs.update(kwargs)
            mock_resp = Mock()
            mock_resp.model_dump.return_value = {
                CHOICES: [{MESSAGE: {"content": "ok", "role": "assistant"}, FINISH_REASON: "stop"}],
                USAGE: {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            return mock_resp
        mock_litellm = Mock()
        mock_litellm.acompletion = AsyncMock(side_effect=capture_acompletion)
        def fake_import(name, *a, **k):
            if name == "litellm":
                return mock_litellm
            return real_import(name, *a, **k)
        model = LiteLLMModel(model_name="openai/gpt-4")
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        config = GenerateContentConfig(max_output_tokens=10)
        config.response_schema = schema
        content = Content(parts=[Part.from_text(text="Hi")], role="user")
        request = LlmRequest(contents=[content], config=config, tools_dict={})
        with patch.object(model, "_ensure_litellm_imported"):
            with patch("builtins.__import__", side_effect=fake_import):
                async for _ in model.generate_async(request, stream=False):
                    break
        assert "response_format" in captured_kwargs
        rf = captured_kwargs["response_format"]
        assert rf["type"] == "json_schema"
        assert "json_schema" in rf
        assert rf["json_schema"]["schema"].get("additionalProperties") is False

    @pytest.mark.asyncio
    async def test_generate_async_passes_response_format_for_gemini_model(self):
        import builtins
        real_import = builtins.__import__
        captured_kwargs = {}
        async def capture_acompletion(**kwargs):
            captured_kwargs.update(kwargs)
            mock_resp = Mock()
            mock_resp.model_dump.return_value = {
                CHOICES: [{MESSAGE: {"content": "ok", "role": "assistant"}, FINISH_REASON: "stop"}],
                USAGE: {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            return mock_resp
        mock_litellm = Mock()
        mock_litellm.acompletion = AsyncMock(side_effect=capture_acompletion)
        def fake_import(name, *a, **k):
            if name == "litellm":
                return mock_litellm
            return real_import(name, *a, **k)
        model = LiteLLMModel(model_name="gemini/gemini-1.5-pro")
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        config = GenerateContentConfig(max_output_tokens=10)
        config.response_schema = schema
        content = Content(parts=[Part.from_text(text="Hi")], role="user")
        request = LlmRequest(contents=[content], config=config, tools_dict={})
        with patch.object(model, "_ensure_litellm_imported"):
            with patch("builtins.__import__", side_effect=fake_import):
                async for _ in model.generate_async(request, stream=False):
                    break
        assert "response_format" in captured_kwargs
        rf = captured_kwargs["response_format"]
        assert rf["type"] == "json_object"
        assert "response_schema" in rf
        assert rf["response_schema"] == schema


class TestGenerateAsyncStream:
    @pytest.mark.asyncio
    async def test_generate_async_stream_yields_partial_then_final(self):
        model = LiteLLMModel(model_name="openai/gpt-4")
        content = Content(parts=[Part.from_text(text="Say hi")], role="user")
        request = LlmRequest(contents=[content], config=None, tools_dict={})

        chunk1 = Mock()
        chunk1.model_dump.return_value = {
            CHOICES: [{DELTA: {CONTENT: "Hello "}}],
            USAGE: None,
        }
        chunk2 = Mock()
        chunk2.model_dump.return_value = {
            CHOICES: [{DELTA: {CONTENT: "world"}}],
            USAGE: None,
        }
        chunk3 = Mock()
        chunk3.model_dump.return_value = {
            CHOICES: [],
            USAGE: {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }

        async def fake_stream(**kwargs):
            yield chunk1
            yield chunk2
            yield chunk3

        import builtins
        real_import = builtins.__import__
        mock_litellm = Mock()
        mock_litellm.acompletion = AsyncMock(return_value=fake_stream())
        def fake_import(name, *a, **k):
            if name == "litellm":
                return mock_litellm
            return real_import(name, *a, **k)
        with patch.object(model, "_ensure_litellm_imported"):
            with patch("builtins.__import__", side_effect=fake_import):
                responses = []
                async for r in model.generate_async(request, stream=True):
                    responses.append(r)
                assert len(responses) >= 2
                partials = [r for r in responses if r.partial]
                finals = [r for r in responses if not r.partial]
                assert len(finals) == 1
                assert finals[0].content is not None
                assert "Hello " in finals[0].content.parts[0].text or "world" in finals[0].content.parts[0].text
                assert finals[0].usage_metadata is not None
                assert finals[0].usage_metadata.total_token_count == 3

    @pytest.mark.asyncio
    async def test_generate_async_stream_includes_stream_options_in_api_params(self):
        model = LiteLLMModel(model_name="openai/gpt-4")
        content = Content(parts=[Part.from_text(text="x")], role="user")
        request = LlmRequest(contents=[content], config=None, tools_dict={})

        captured_kwargs = {}

        async def capture_acompletion(**kwargs):
            captured_kwargs.update(kwargs)
            async def empty_stream():
                yield None
            return empty_stream()

        import builtins
        real_import = builtins.__import__
        mock_litellm = Mock()
        mock_litellm.acompletion = AsyncMock(side_effect=capture_acompletion)
        def fake_import(name, *a, **k):
            if name == "litellm":
                return mock_litellm
            return real_import(name, *a, **k)
        with patch.object(model, "_ensure_litellm_imported"):
            with patch("builtins.__import__", side_effect=fake_import):
                async for _ in model.generate_async(request, stream=True):
                    break
        assert captured_kwargs.get("stream") is True
        assert captured_kwargs.get("stream_options") == {"include_usage": True}

    @pytest.mark.asyncio
    async def test_generate_async_stream_exception_yields_streaming_error(self):
        model = LiteLLMModel(model_name="openai/gpt-4")
        content = Content(parts=[Part.from_text(text="x")], role="user")
        request = LlmRequest(contents=[content], config=None, tools_dict={})

        async def failing_stream(**kwargs):
            raise RuntimeError("Stream broken")

        import builtins
        real_import = builtins.__import__
        mock_litellm = Mock()
        mock_litellm.acompletion = AsyncMock(side_effect=failing_stream)
        def fake_import(name, *a, **k):
            if name == "litellm":
                return mock_litellm
            return real_import(name, *a, **k)
        with patch.object(model, "_ensure_litellm_imported"):
            with patch("builtins.__import__", side_effect=fake_import):
                responses = []
                async for r in model.generate_async(request, stream=True):
                    responses.append(r)
                assert len(responses) == 1
                assert responses[0].error_code == "STREAMING_ERROR"
                assert "Stream broken" in (responses[0].error_message or "")


class TestLiteLLMModelValidateRequest:
    def test_validate_request_empty_contents_raises(self):
        model = LiteLLMModel(model_name="openai/gpt-4")
        request = LlmRequest(contents=[], config=None, tools_dict={})
        with pytest.raises(ValueError, match="At least one content"):
            model.validate_request(request)

    def test_validate_request_valid_passes(self):
        model = LiteLLMModel(model_name="openai/gpt-4")
        content = Content(parts=[Part.from_text(text="Hi")], role="user")
        request = LlmRequest(contents=[content], config=None, tools_dict={})
        model.validate_request(request)
