# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
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
            USAGE: {
                "prompt_tokens": 1,
                "completion_tokens": 2,
                "total_tokens": 3
            },
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
            USAGE: {
                "prompt_tokens": 5,
                "completion_tokens": 0,
                "total_tokens": 5
            },
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
                MESSAGE: {
                    "content": "Hi there",
                    "role": "assistant"
                },
                FINISH_REASON: "stop",
            }],
            USAGE: {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12
            },
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
                MESSAGE: {
                    "content": "x",
                    "role": "assistant"
                },
                FINISH_REASON: "length",
            }],
            USAGE: {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2
            },
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
                    "content":
                    None,
                    "role":
                    "assistant",
                    TOOL_CALLS: [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "NYC"}'
                        },
                    }],
                },
                FINISH_REASON: "tool_calls",
            }],
            USAGE: {
                "prompt_tokens": 2,
                "completion_tokens": 3,
                "total_tokens": 5
            },
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
            USAGE: {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            },
        }
        r = model._create_response_with_content(response_dict, partial=False)
        assert r.error_code == "NO_MESSAGE"
        assert r.usage_metadata is not None

    def test_empty_parts_yields_single_empty_text_part_with_thought_false(self):
        model = LiteLLMModel(model_name="openai/gpt-4")
        response_dict = {
            CHOICES: [{
                MESSAGE: {
                    "content": "",
                    "role": "assistant"
                },
                FINISH_REASON: "stop",
            }],
            USAGE: {
                "prompt_tokens": 1,
                "completion_tokens": 0,
                "total_tokens": 1
            },
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
            safety_settings=[{
                "category": "HARM",
                "threshold": "BLOCK_MEDIUM"
            }],
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
                MESSAGE: {
                    "content": "Hi!",
                    "role": "assistant"
                },
                FINISH_REASON: "stop",
            }],
            USAGE: {
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "total_tokens": 3
            },
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
                assert responses[0].custom_metadata == {"error": responses[0].error_message}

    @pytest.mark.asyncio
    async def test_generate_async_passes_response_format_for_openai_model(self):
        import builtins
        real_import = builtins.__import__
        captured_kwargs = {}

        async def capture_acompletion(**kwargs):
            captured_kwargs.update(kwargs)
            mock_resp = Mock()
            mock_resp.model_dump.return_value = {
                CHOICES: [{
                    MESSAGE: {
                        "content": "ok",
                        "role": "assistant"
                    },
                    FINISH_REASON: "stop"
                }],
                USAGE: {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2
                },
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
                CHOICES: [{
                    MESSAGE: {
                        "content": "ok",
                        "role": "assistant"
                    },
                    FINISH_REASON: "stop"
                }],
                USAGE: {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2
                },
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
            CHOICES: [{
                DELTA: {
                    CONTENT: "Hello "
                }
            }],
            USAGE: None,
        }
        chunk2 = Mock()
        chunk2.model_dump.return_value = {
            CHOICES: [{
                DELTA: {
                    CONTENT: "world"
                }
            }],
            USAGE: None,
        }
        chunk3 = Mock()
        chunk3.model_dump.return_value = {
            CHOICES: [],
            USAGE: {
                "prompt_tokens": 1,
                "completion_tokens": 2,
                "total_tokens": 3
            },
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
                assert responses[0].custom_metadata == {"error": responses[0].error_message}


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


# ===========================================================================
# Prompt cache — provider family classification
# ===========================================================================


class TestLiteLLMCacheFamily:
    """_litellm_cache_family must route known prefixes to the correct family."""

    def _family(self, model_name: str):
        from trpc_agent_sdk.models._litellm_model import _litellm_cache_family
        return _litellm_cache_family(model_name)

    def test_anthropic_prefix_is_anthropic_family(self):
        assert self._family("anthropic/claude-3-5-sonnet") == "anthropic"

    def test_bedrock_prefix_is_anthropic_family(self):
        assert self._family("bedrock/anthropic.claude-3-haiku") == "anthropic"

    def test_vertex_ai_prefix_is_anthropic_family(self):
        assert self._family("vertex_ai/claude-3-opus") == "anthropic"

    def test_vertex_ai_beta_prefix_is_anthropic_family(self):
        assert self._family("vertex_ai_beta/gemini-1.5") == "anthropic"

    def test_gemini_prefix_is_anthropic_family(self):
        assert self._family("gemini/gemini-1.5-flash") == "anthropic"

    def test_openai_prefix_is_openai_managed_family(self):
        assert self._family("openai/gpt-4o") == "openai_managed"

    def test_azure_prefix_is_openai_managed_family(self):
        assert self._family("azure/gpt-35-turbo") == "openai_managed"

    def test_deepseek_prefix_is_openai_managed_family(self):
        assert self._family("deepseek/deepseek-chat") == "openai_managed"

    def test_xai_prefix_is_openai_managed_family(self):
        assert self._family("xai/grok-1") == "openai_managed"

    def test_unknown_prefix_returns_none(self):
        assert self._family("unknown/some-model") is None

    def test_groq_prefix_returns_none(self):
        """groq is not in either cache family list."""
        assert self._family("groq/llama-3") is None

    def test_prefix_matching_is_case_insensitive(self):
        assert self._family("ANTHROPIC/claude-3") == "anthropic"
        assert self._family("OpenAI/gpt-4") == "openai_managed"


# ===========================================================================
# Prompt cache — Anthropic-family (non-Bedrock) request shaping
# ===========================================================================


class TestLiteLLMApplyPromptCacheAnthropicFamily:
    """For anthropic-family non-Bedrock models, _apply_prompt_cache should:
    - stamp cache_control on the last tool directly (not via injection_points),
    - add cache_control_injection_points for system / messages breakpoints.
    """

    def _model(self, model_name: str = "anthropic/claude-3-5-sonnet", **kw):
        from trpc_agent_sdk.configs import PromptCacheConfig
        kw.setdefault("prompt_cache_config",
                      PromptCacheConfig(
                          enabled=True,
                          ttl="1h",
                          breakpoints=["tools", "system", "messages"],
                      ))
        return LiteLLMModel(model_name=model_name, api_key="k", **kw)

    def test_tools_breakpoint_stamps_last_tool_directly(self):
        """Non-Bedrock anthropic provider stamps cache_control on tools[-1] directly."""
        model = self._model()
        api_params = {
            "tools": [{
                "name": "t1"
            }, {
                "name": "t2"
            }],
            "messages": [],
        }
        model._apply_prompt_cache(api_params, None)
        assert "cache_control" not in api_params["tools"][0]
        assert api_params["tools"][-1]["cache_control"]["type"] == "ephemeral"
        assert api_params["tools"][-1]["cache_control"]["ttl"] == "1h"

    def test_injection_points_added_for_system_and_messages(self):
        """cache_control_injection_points contains entries for system and latest assistant message."""
        model = self._model()
        api_params = {
            "tools": [],
            "messages": [
                {
                    "role": "user",
                    "content": "hi"
                },
                {
                    "role": "assistant",
                    "content": "hello"
                },
                {
                    "role": "user",
                    "content": "again"
                },
            ],
        }
        model._apply_prompt_cache(api_params, None)
        points = api_params.get("cache_control_injection_points", [])
        locations = {p.get("location") for p in points}
        roles = {p.get("role") for p in points if "role" in p}
        assert "message" in locations
        assert "system" in roles
        index_points = [p for p in points if p.get("index") == 1]
        assert len(index_points) == 1

    def test_disabled_config_leaves_api_params_unchanged(self):
        from trpc_agent_sdk.configs import PromptCacheConfig
        model = LiteLLMModel(
            model_name="anthropic/claude-3-5-sonnet",
            api_key="k",
            prompt_cache_config=PromptCacheConfig(enabled=False),
        )
        api_params = {"tools": [{"name": "t1"}]}
        model._apply_prompt_cache(api_params, None)
        assert "cache_control" not in api_params["tools"][0]
        assert "cache_control_injection_points" not in api_params

    def test_empty_breakpoints_leaves_api_params_unchanged(self):
        from trpc_agent_sdk.configs import PromptCacheConfig
        model = LiteLLMModel(
            model_name="anthropic/claude-3-5-sonnet",
            api_key="k",
            prompt_cache_config=PromptCacheConfig(enabled=True, breakpoints=[]),
        )
        api_params = {"tools": [{"name": "t1"}]}
        model._apply_prompt_cache(api_params, None)
        assert "cache_control" not in api_params["tools"][0]
        assert "cache_control_injection_points" not in api_params

    def test_anthropic_family_ttl_is_forwarded_to_litellm(self):
        """Anthropic-family LiteLLM routes pass TTL through for provider adapters to handle."""
        from trpc_agent_sdk.configs import PromptCacheConfig
        model = LiteLLMModel(
            model_name="anthropic/claude-3-5-sonnet",
            api_key="k",
            prompt_cache_config=PromptCacheConfig(enabled=True, ttl="in_memory", breakpoints=["tools"]),
        )
        api_params = {"tools": [{"name": "t1"}]}

        model._apply_prompt_cache(api_params, None)

        assert api_params["tools"][-1]["cache_control"] == {
            "type": "ephemeral",
            "ttl": "in_memory",
        }


# ===========================================================================
# Prompt cache — Bedrock request shaping
# ===========================================================================


class TestLiteLLMApplyPromptCacheBedrock:
    """For Bedrock models, tool-level cache_control must NOT be set directly;
    instead a tool_config injection_point is added."""

    def _model(self):
        from trpc_agent_sdk.configs import PromptCacheConfig
        return LiteLLMModel(
            model_name="bedrock/anthropic.claude-3-haiku",
            api_key="k",
            prompt_cache_config=PromptCacheConfig(
                enabled=True,
                breakpoints=["tools", "system"],
            ),
        )

    def test_bedrock_does_not_stamp_tool_directly(self):
        """Bedrock models must NOT get cache_control on the tool dict itself."""
        model = self._model()
        api_params = {"tools": [{"name": "t1"}], "messages": []}
        model._apply_prompt_cache(api_params, None)
        assert "cache_control" not in api_params["tools"][0]

    def test_bedrock_adds_tool_config_injection_point(self):
        """Bedrock tool cachePoint is expressed as {"location": "tool_config"}."""
        model = self._model()
        api_params = {"tools": [{"name": "t1"}], "messages": []}
        model._apply_prompt_cache(api_params, None)
        points = api_params.get("cache_control_injection_points", [])
        tool_config_points = [p for p in points if p.get("location") == "tool_config"]
        assert len(tool_config_points) == 1

    def test_bedrock_system_injection_point_present(self):
        """system injection point is added for Bedrock, same as non-Bedrock."""
        model = self._model()
        api_params = {"tools": [{"name": "t1"}], "messages": []}
        model._apply_prompt_cache(api_params, None)
        points = api_params.get("cache_control_injection_points", [])
        system_points = [p for p in points if p.get("role") == "system"]
        assert len(system_points) == 1


# ===========================================================================
# Prompt cache — OpenAI-managed family request shaping
# ===========================================================================


class TestLiteLLMApplyPromptCacheOpenAIFamily:
    """For openai-managed-family models, cache config is routed as top-level LiteLLM params."""

    def _model(self, model_name: str, **kw):
        from trpc_agent_sdk.configs import PromptCacheConfig
        kw.setdefault("prompt_cache_config", PromptCacheConfig(
            enabled=True,
            prompt_cache_key="my-key",
            ttl="24h",
        ))
        return LiteLLMModel(model_name=model_name, api_key="k", **kw)

    def test_openai_cache_key_and_retention_written_to_top_level_params(self):
        """prompt_cache_key and prompt_cache_retention are top-level LiteLLM params."""
        model = self._model("openai/gpt-4o")
        api_params: dict = {}
        model._apply_prompt_cache(api_params, None)
        assert api_params.get("prompt_cache_key") == "my-key"
        assert api_params.get("prompt_cache_retention") == "24h"
        assert "extra_body" not in api_params

    def test_openai_existing_extra_body_is_preserved(self):
        """Pre-existing extra_body dict entries are preserved when cache keys are added."""
        from trpc_agent_sdk.configs import PromptCacheConfig
        model = LiteLLMModel(
            model_name="openai/gpt-4o",
            api_key="k",
            prompt_cache_config=PromptCacheConfig(
                enabled=True,
                prompt_cache_key="new-key",
            ),
        )
        api_params = {"extra_body": {"user": "alice"}}
        model._apply_prompt_cache(api_params, None)
        assert api_params["extra_body"] == {"user": "alice"}
        assert api_params["prompt_cache_key"] == "new-key"

    def test_openai_custom_ttl_is_forwarded(self):
        """OpenAI-family LiteLLM routes pass TTL through for provider adapters to handle."""
        from trpc_agent_sdk.configs import PromptCacheConfig
        model = LiteLLMModel(
            model_name="openai/gpt-4o",
            api_key="k",
            prompt_cache_config=PromptCacheConfig(enabled=True, ttl="1h"),
        )
        api_params: dict = {}
        model._apply_prompt_cache(api_params, None)
        assert api_params.get("prompt_cache_retention") == "1h"


# ===========================================================================
# Prompt cache — Azure OpenAI (skips prompt_cache_retention)
# ===========================================================================


class TestLiteLLMApplyPromptCacheAzure:
    """Azure OpenAI supports prompt_cache_key but not prompt_cache_retention."""

    def _model(self):
        from trpc_agent_sdk.configs import PromptCacheConfig
        return LiteLLMModel(
            model_name="azure/gpt-35-turbo",
            api_key="k",
            prompt_cache_config=PromptCacheConfig(
                enabled=True,
                prompt_cache_key="az-key",
                ttl="24h",
            ),
        )

    def test_azure_sets_cache_key(self):
        """prompt_cache_key is forwarded for Azure."""
        model = self._model()
        api_params: dict = {}
        model._apply_prompt_cache(api_params, None)
        assert api_params.get("prompt_cache_key") == "az-key"

    def test_azure_does_not_set_prompt_cache_retention(self):
        """prompt_cache_retention must NOT be set for Azure, even when TTL is provided."""
        model = self._model()
        api_params: dict = {}
        model._apply_prompt_cache(api_params, None)
        assert "prompt_cache_retention" not in api_params


# ===========================================================================
# Prompt cache — unknown provider family
# ===========================================================================


class TestLiteLLMApplyPromptCacheUnknownFamily:
    """Unknown provider prefix with enabled cache config must warn and leave params clean."""

    def test_unknown_prefix_logs_warning_and_leaves_params_unchanged(self):
        from trpc_agent_sdk.configs import PromptCacheConfig
        model = LiteLLMModel(
            model_name="groq/llama-3",
            api_key="k",
            prompt_cache_config=PromptCacheConfig(enabled=True, ttl="1h"),
        )
        api_params: dict = {"tools": [{"name": "t1"}]}
        with patch("trpc_agent_sdk.models._litellm_model.logger") as mock_log:
            model._apply_prompt_cache(api_params, None)
        mock_log.warning.assert_called_once()
        assert "cache_control" not in api_params.get("tools", [{}])[0]
        assert "cache_control_injection_points" not in api_params
        assert "extra_body" not in api_params


# ===========================================================================
# Model retry hooks
# ===========================================================================


class _LiteLLMRetryTestError(Exception):

    def __init__(self, status_code=None, headers=None):
        super().__init__(f"status {status_code}" if status_code is not None else "retry test")
        if status_code is not None:
            self.status_code = status_code
        if headers is not None:
            self.litellm_response_headers = headers


class TestLiteLLMRetryHooks:

    def _model(self):
        return LiteLLMModel(model_name="openai/gpt-4", api_key="k")

    def test_x_should_retry_header_has_priority(self):
        model = self._model()
        assert model._get_model_retry_info(_LiteLLMRetryTestError(400, {"x-should-retry": "true"})).should_retry is True
        assert model._get_model_retry_info(_LiteLLMRetryTestError(500, {"x-should-retry": "false"})).should_retry is False

    @pytest.mark.parametrize("status_code", [408, 409, 429, 500, 503])
    def test_retryable_status_codes(self, status_code):
        assert self._model()._get_model_retry_info(_LiteLLMRetryTestError(status_code)).should_retry is True

    @pytest.mark.parametrize("status_code", [400, 401, 403, 404, 499])
    def test_non_retryable_status_codes(self, status_code):
        assert self._model()._get_model_retry_info(_LiteLLMRetryTestError(status_code)).should_retry is False

    def test_missing_response_status_not_retried(self):
        assert self._model()._get_model_retry_info(ValueError("boom")).should_retry is False

    def test_retry_after_extracted_from_litellm_headers(self):
        info = self._model()._get_model_retry_info(_LiteLLMRetryTestError(429, {"retry-after": "3"}))
        assert info.should_retry is True
        assert info.retry_after == 3.0


# ===========================================================================
# Prompt cache — _set_extra_body utility
# ===========================================================================


class TestLiteLLMSetExtraBody:
    """_set_extra_body merges keys into api_params['extra_body'] correctly."""

    def _set(self, api_params: dict, key: str, value) -> None:
        LiteLLMModel._set_extra_body(api_params, key, value)

    def test_creates_extra_body_dict_when_absent(self):
        api_params: dict = {}
        self._set(api_params, "foo", "bar")
        assert api_params["extra_body"] == {"foo": "bar"}

    def test_merges_into_existing_extra_body(self):
        api_params = {"extra_body": {"x": 1}}
        self._set(api_params, "y", 2)
        assert api_params["extra_body"] == {"x": 1, "y": 2}

    def test_replaces_non_dict_extra_body_with_warning(self):
        api_params = {"extra_body": "invalid"}
        with patch("trpc_agent_sdk.models._litellm_model.logger") as mock_log:
            self._set(api_params, "k", "v")
        mock_log.warning.assert_called_once()
        assert api_params["extra_body"] == {"k": "v"}


# ===========================================================================
# Prompt cache — _build_cache_injection_points
# ===========================================================================


class TestLiteLLMBuildCacheInjectionPoints:
    """_build_cache_injection_points returns the correct point descriptors."""

    def _build(self, model_name: str, breakpoints: list, ttl=None, messages=None):
        model = LiteLLMModel(model_name=model_name, api_key="k")
        return model._build_cache_injection_points(breakpoints, ttl, messages)

    def test_system_breakpoint_adds_message_role_system(self):
        points = self._build("anthropic/claude-3", ["system"])
        assert any(p.get("role") == "system" for p in points)

    def test_messages_breakpoint_adds_latest_assistant_index(self):
        messages = [
            {
                "role": "user",
                "content": "hi"
            },
            {
                "role": "assistant",
                "content": "hello"
            },
            {
                "role": "user",
                "content": "again"
            },
        ]
        points = self._build("anthropic/claude-3", ["messages"], messages=messages)
        assert any(p.get("index") == 1 for p in points)

    def test_messages_breakpoint_without_assistant_adds_nothing(self):
        messages = [{"role": "user", "content": "hi"}]
        points = self._build("anthropic/claude-3", ["messages"], messages=messages)
        assert points == []

    def test_tools_breakpoint_bedrock_adds_tool_config(self):
        points = self._build("bedrock/anthropic.claude", ["tools"])
        assert any(p.get("location") == "tool_config" for p in points)

    def test_tools_breakpoint_non_bedrock_adds_nothing(self):
        """For non-Bedrock providers, tools are stamped directly on the tool; no injection point."""
        points = self._build("anthropic/claude-3", ["tools"])
        assert not any(p.get("location") == "tool_config" for p in points)

    def test_ttl_is_included_in_control_dict(self):
        points = self._build("anthropic/claude-3", ["system"], ttl="1h")
        system_points = [p for p in points if p.get("role") == "system"]
        assert len(system_points) == 1
        assert system_points[0]["control"]["ttl"] == "1h"

    def test_no_ttl_produces_ephemeral_only_control(self):
        points = self._build("anthropic/claude-3", ["system"], ttl=None)
        system_points = [p for p in points if p.get("role") == "system"]
        assert system_points[0]["control"] == {"type": "ephemeral"}

    def test_all_non_bedrock_breakpoints_no_tool_config_point(self):
        """All three breakpoints for a non-Bedrock provider: no tool_config point."""
        messages = [
            {
                "role": "user",
                "content": "hi"
            },
            {
                "role": "assistant",
                "content": "hello"
            },
        ]
        points = self._build("anthropic/claude-3", ["tools", "system", "messages"], messages=messages)
        assert not any(p.get("location") == "tool_config" for p in points)
        assert any(p.get("role") == "system" for p in points)
        assert any(p.get("index") == 1 for p in points)
