# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import base64
import json
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from trpc_agent_sdk.models import LlmRequest, OpenAIModel
from trpc_agent_sdk.models._llm_response import LlmResponse
from trpc_agent_sdk.models._openai_model import (
    ApiParamsKey,
    FinishReason,
    ToolCall,
    ToolKey,
)
from trpc_agent_sdk.types import (
    Content,
    FunctionCall,
    FunctionDeclaration,
    FunctionResponse,
    GenerateContentConfig,
    Part,
    Schema,
    ThinkingConfig,
    Tool,
    Type,
)


def _model(**kwargs):
    """Shortcut to create an OpenAIModel with minimal defaults."""
    kwargs.setdefault("model_name", "gpt-4")
    kwargs.setdefault("api_key", "test_key")
    return OpenAIModel(**kwargs)


def _request(contents, config=None, tools_dict=None, streaming_tool_names=None):
    """Shortcut to create an LlmRequest."""
    r = LlmRequest(contents=contents, config=config, tools_dict=tools_dict or {})
    if streaming_tool_names is not None:
        r.streaming_tool_names = streaming_tool_names
    return r


# ---------------------------------------------------------------------------
# _parse_finish_reason
# ---------------------------------------------------------------------------


class TestParseFinishReason:
    """Tests for _parse_finish_reason helper."""

    def test_stop(self):
        """Valid stop reason returns STOP."""
        model = _model()
        assert model._parse_finish_reason("stop") == FinishReason.STOP

    def test_length(self):
        """Valid length reason returns LENGTH."""
        model = _model()
        assert model._parse_finish_reason("length") == FinishReason.LENGTH

    def test_tool_calls(self):
        """Valid tool_calls reason returns TOOL_CALLS."""
        model = _model()
        assert model._parse_finish_reason("tool_calls") == FinishReason.TOOL_CALLS

    def test_unknown_reason_returns_error(self):
        """Unknown finish reason maps to ERROR."""
        model = _model()
        assert model._parse_finish_reason("unknown_xyz") == FinishReason.ERROR


# ---------------------------------------------------------------------------
# _verify_text_content_in_delta_response
# ---------------------------------------------------------------------------


class TestVerifyTextContentInDeltaResponse:
    """Tests for _verify_text_content_in_delta_response."""

    def test_content_present(self):
        """Returns True when delta has non-empty content."""
        model = _model()
        resp = {"choices": [{"delta": {"content": "hello"}}]}
        assert model._verify_text_content_in_delta_response(resp) is True

    def test_content_empty_string(self):
        """Returns False when delta content is empty string."""
        model = _model()
        resp = {"choices": [{"delta": {"content": ""}}]}
        assert model._verify_text_content_in_delta_response(resp) is False

    def test_content_none(self):
        """Returns False when delta content is None."""
        model = _model()
        resp = {"choices": [{"delta": {"content": None}}]}
        assert model._verify_text_content_in_delta_response(resp) is False

    def test_reasoning_content_present(self):
        """Returns True when reasoning_content exists."""
        model = _model()
        resp = {"choices": [{"delta": {"reasoning_content": "thinking..."}}]}
        assert model._verify_text_content_in_delta_response(resp) is True

    def test_choices_none(self):
        """Returns False when choices is None (DeepSeek edge case)."""
        model = _model()
        resp = {"choices": None}
        assert model._verify_text_content_in_delta_response(resp) is False

    def test_choices_empty_list(self):
        """Returns False when choices is empty list."""
        model = _model()
        resp = {"choices": []}
        assert model._verify_text_content_in_delta_response(resp) is False

    def test_delta_none(self):
        """Returns False when delta is None."""
        model = _model()
        resp = {"choices": [{"delta": None}]}
        assert model._verify_text_content_in_delta_response(resp) is False


# ---------------------------------------------------------------------------
# _is_thinking_event / _get_thinking_state
# ---------------------------------------------------------------------------


class TestThinkingEvent:
    """Tests for _is_thinking_event and _get_thinking_state."""

    def test_is_thinking_event_true(self):
        """Returns True for a thinking start event."""
        model = _model()
        resp = {"object": "stream_server.event", "event": {"name": "thinking", "state": 0}}
        assert model._is_thinking_event(resp) is True

    def test_is_thinking_event_false(self):
        """Returns False for a regular chunk."""
        model = _model()
        resp = {"object": "chat.completion.chunk", "event": {}}
        assert model._is_thinking_event(resp) is False

    def test_get_thinking_state_start(self):
        """State 0 means thinking started."""
        model = _model()
        resp = {"object": "stream_server.event", "event": {"name": "thinking", "state": 0}}
        assert model._get_thinking_state(resp) == 0

    def test_get_thinking_state_end(self):
        """State 2 means thinking ended."""
        model = _model()
        resp = {"object": "stream_server.event", "event": {"name": "thinking", "state": 2}}
        assert model._get_thinking_state(resp) == 2

    def test_get_thinking_state_non_thinking(self):
        """Returns -1 for non-thinking event."""
        model = _model()
        resp = {"object": "chat.completion.chunk"}
        assert model._get_thinking_state(resp) == -1


# ---------------------------------------------------------------------------
# _process_usage
# ---------------------------------------------------------------------------


class TestProcessUsage:
    """Tests for _process_usage."""

    def test_with_usage_data(self):
        """Parses token counts from usage dict."""
        model = _model()
        chunk = {"usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
        usage = model._process_usage(chunk)
        assert usage is not None
        assert usage.prompt_token_count == 10
        assert usage.candidates_token_count == 20
        assert usage.total_token_count == 30

    def test_without_usage_data(self):
        """Returns None when usage key is absent."""
        model = _model()
        assert model._process_usage({}) is None

    def test_usage_none(self):
        """Returns None when usage value is None."""
        model = _model()
        assert model._process_usage({"usage": None}) is None


# ---------------------------------------------------------------------------
# _process_tool_call_delta
# ---------------------------------------------------------------------------


class TestProcessToolCallDelta:
    """Tests for _process_tool_call_delta."""

    def test_first_delta_creates_slot(self):
        """A delta at index 0 creates the first slot."""
        model = _model()
        acc: list[dict] = []
        delta = {
            ToolKey.ID: "call_abc",
            ToolKey.FUNCTION: {ToolKey.NAME: "get_weather", ToolKey.ARGUMENTS: '{"city":'},
        }
        model._process_tool_call_delta(delta, acc)
        assert len(acc) == 1
        assert acc[0][ToolKey.ID] == "call_abc"
        assert acc[0][ToolKey.FUNCTION][ToolKey.NAME] == "get_weather"
        assert acc[0][ToolKey.FUNCTION][ToolKey.ARGUMENTS] == '{"city":'

    def test_subsequent_delta_appends_arguments(self):
        """Subsequent deltas append to the argument string."""
        model = _model()
        acc: list[dict] = [{
            ToolKey.ID: "call_abc",
            ToolKey.TYPE: ToolKey.FUNCTION,
            ToolKey.FUNCTION: {ToolKey.NAME: "f", ToolKey.ARGUMENTS: '{"a":'},
            ToolKey.THOUGHT_SIGNATURE: "",
        }]
        delta = {
            "index": 0,
            ToolKey.FUNCTION: {ToolKey.ARGUMENTS: '"b"}'},
        }
        model._process_tool_call_delta(delta, acc)
        assert acc[0][ToolKey.FUNCTION][ToolKey.ARGUMENTS] == '{"a":"b"}'

    def test_none_id_preserves_existing(self):
        """A delta with None ID keeps the existing ID."""
        model = _model()
        acc: list[dict] = [{
            ToolKey.ID: "call_orig",
            ToolKey.TYPE: ToolKey.FUNCTION,
            ToolKey.FUNCTION: {ToolKey.NAME: "f", ToolKey.ARGUMENTS: ""},
            ToolKey.THOUGHT_SIGNATURE: "",
        }]
        delta = {"index": 0, ToolKey.ID: None, ToolKey.FUNCTION: {ToolKey.ARGUMENTS: "x"}}
        model._process_tool_call_delta(delta, acc)
        assert acc[0][ToolKey.ID] == "call_orig"

    def test_thought_signature_from_provider_specific_fields(self):
        """Extracts thought_signature from provider_specific_fields."""
        model = _model()
        acc: list[dict] = []
        delta = {
            "index": 0,
            ToolKey.PROVIDER_SPECIFIC_FIELDS: {ToolKey.THOUGHT_SIGNATURE: "sig123"},
            ToolKey.FUNCTION: {ToolKey.NAME: "f", ToolKey.ARGUMENTS: "{}"},
        }
        model._process_tool_call_delta(delta, acc)
        assert acc[0][ToolKey.THOUGHT_SIGNATURE] == "sig123"


# ---------------------------------------------------------------------------
# _create_complete_tool_calls
# ---------------------------------------------------------------------------


class TestCreateCompleteToolCalls:
    """Tests for _create_complete_tool_calls."""

    def test_empty_list_returns_none(self):
        """Returns None for empty accumulated list."""
        model = _model()
        assert model._create_complete_tool_calls([]) is None

    def test_valid_tool_call(self):
        """Valid accumulated data produces ToolCall objects."""
        model = _model()
        acc = [{
            ToolKey.ID: "call_1",
            ToolKey.FUNCTION: {ToolKey.NAME: "search", ToolKey.ARGUMENTS: '{"q":"test"}'},
            ToolKey.THOUGHT_SIGNATURE: None,
        }]
        result = model._create_complete_tool_calls(acc)
        assert result is not None
        assert len(result) == 1
        assert result[0].name == "search"
        assert result[0].arguments == {"q": "test"}

    def test_incomplete_json_skipped(self):
        """Incomplete JSON arguments are skipped."""
        model = _model()
        acc = [{
            ToolKey.ID: "call_1",
            ToolKey.FUNCTION: {ToolKey.NAME: "f", ToolKey.ARGUMENTS: '{"incomplete":'},
        }]
        assert model._create_complete_tool_calls(acc) is None

    def test_empty_arguments_yields_empty_dict(self):
        """Empty argument string produces empty dict."""
        model = _model()
        acc = [{
            ToolKey.ID: "call_1",
            ToolKey.FUNCTION: {ToolKey.NAME: "f", ToolKey.ARGUMENTS: ""},
        }]
        result = model._create_complete_tool_calls(acc)
        assert result is not None
        assert result[0].arguments == {}

    def test_missing_id_generates_fallback(self):
        """Missing ID gets a generated fallback."""
        model = _model()
        acc = [{
            ToolKey.ID: "",
            ToolKey.FUNCTION: {ToolKey.NAME: "f", ToolKey.ARGUMENTS: "{}"},
        }]
        result = model._create_complete_tool_calls(acc)
        assert result is not None
        assert result[0].id.startswith("call_")

    def test_missing_name_skipped(self):
        """Entry without a function name is skipped."""
        model = _model()
        acc = [{
            ToolKey.ID: "call_1",
            ToolKey.FUNCTION: {ToolKey.NAME: "", ToolKey.ARGUMENTS: "{}"},
        }]
        assert model._create_complete_tool_calls(acc) is None


# ---------------------------------------------------------------------------
# _verify_text_content_in_openai_message_response
# ---------------------------------------------------------------------------


class TestVerifyTextContentInMessageResponse:
    """Tests for _verify_text_content_in_openai_message_response."""

    def test_has_content(self):
        """Returns True when message has content key."""
        model = _model()
        resp = {"choices": [{"message": {"content": "hi"}}]}
        assert model._verify_text_content_in_openai_message_response(resp) is True

    def test_no_message_key(self):
        """Returns False when message key is missing."""
        model = _model()
        resp = {"choices": [{"delta": {"content": "hi"}}]}
        assert model._verify_text_content_in_openai_message_response(resp) is False

    def test_choices_none(self):
        """Returns False when choices is None."""
        model = _model()
        resp = {"choices": None}
        assert model._verify_text_content_in_openai_message_response(resp) is False

    def test_allow_content_none_true(self):
        """With allow_content_none=True, returns True if message exists."""
        model = _model()
        resp = {"choices": [{"message": {}}]}
        assert model._verify_text_content_in_openai_message_response(resp, allow_content_none=True) is True


# ---------------------------------------------------------------------------
# _process_tool_calls_from_message
# ---------------------------------------------------------------------------


class TestProcessToolCallsFromMessage:
    """Tests for _process_tool_calls_from_message."""

    def test_no_tool_calls(self):
        """Returns None when no tool_calls present."""
        model = _model()
        assert model._process_tool_calls_from_message({}) is None

    def test_valid_tool_calls(self):
        """Parses well-formed tool calls from message."""
        model = _model()
        message = {
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "search", "arguments": '{"q":"hello"}'},
            }]
        }
        result = model._process_tool_calls_from_message(message)
        assert result is not None
        assert len(result) == 1
        assert result[0].name == "search"
        assert result[0].arguments == {"q": "hello"}

    def test_malformed_json_skipped(self):
        """Malformed JSON arguments cause the tool call to be skipped."""
        model = _model()
        message = {
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "f", "arguments": "NOT_JSON"},
            }]
        }
        assert model._process_tool_calls_from_message(message) is None

    def test_none_entry_skipped(self):
        """None entries in tool_calls list are skipped."""
        model = _model()
        message = {"tool_calls": [None]}
        assert model._process_tool_calls_from_message(message) is None

    def test_thought_signature_from_provider_specific(self):
        """Extracts thought_signature from provider_specific_fields."""
        model = _model()
        message = {
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "f", "arguments": "{}"},
                "provider_specific_fields": {"thought_signature": "sig_abc"},
            }]
        }
        result = model._process_tool_calls_from_message(message)
        assert result is not None
        assert result[0].thought_signature == "sig_abc"


# ---------------------------------------------------------------------------
# _process_usage_from_response
# ---------------------------------------------------------------------------


class TestProcessUsageFromResponse:
    """Tests for _process_usage_from_response."""

    def test_with_usage(self):
        """Correctly maps usage fields."""
        model = _model()
        resp = {"usage": {"prompt_tokens": 5, "completion_tokens": 15, "total_tokens": 20}}
        usage = model._process_usage_from_response(resp)
        assert usage is not None
        assert usage.prompt_token_count == 5

    def test_without_usage(self):
        """Returns None when usage key is absent."""
        model = _model()
        assert model._process_usage_from_response({}) is None


# ---------------------------------------------------------------------------
# _create_response_without_content / _create_response_with_content
# ---------------------------------------------------------------------------


class TestCreateResponseHelpers:
    """Tests for _create_response_without_content and _create_response_with_content."""

    def test_without_content_stop(self):
        """No error_code when finish_reason is stop."""
        model = _model()
        resp = {
            "id": "resp_1",
            "choices": [{"finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }
        result = model._create_response_without_content(resp)
        assert result.content is None
        assert result.error_code is None
        assert result.response_id == "resp_1"

    def test_without_content_length(self):
        """Error_code set when finish_reason is length."""
        model = _model()
        resp = {"id": "r2", "choices": [{"finish_reason": "length"}]}
        result = model._create_response_without_content(resp)
        assert result.error_code == "length"

    def test_with_content_text_only(self):
        """Text content response has text part."""
        model = _model()
        resp = {
            "id": "r3",
            "choices": [{"message": {"content": "hello", "role": "assistant"}, "finish_reason": "stop"}],
        }
        result = model._create_response_with_content(resp)
        assert result.content is not None
        assert result.content.parts[0].text == "hello"

    def test_with_content_tool_calls(self):
        """Response with tool calls creates function_call parts."""
        model = _model()
        resp = {
            "choices": [{
                "message": {
                    "content": None,
                    "role": "assistant",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "search", "arguments": '{"q":"hi"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }
        result = model._create_response_with_content(resp)
        fc_parts = [p for p in result.content.parts if p.function_call]
        assert len(fc_parts) == 1
        assert fc_parts[0].function_call.name == "search"

    def test_with_content_empty_fallback(self):
        """Response with no text and no tool calls produces empty text part."""
        model = _model()
        resp = {
            "choices": [{"message": {"content": None, "role": "assistant"}, "finish_reason": "stop"}],
        }
        result = model._create_response_with_content(resp)
        assert result.content is not None
        assert result.content.parts[0].text == ""


# ---------------------------------------------------------------------------
# _convert_tools_to_openai_format
# ---------------------------------------------------------------------------


class TestConvertToolsToOpenAIFormat:
    """Tests for _convert_tools_to_openai_format."""

    def test_basic_function_declaration(self):
        """Converts a FunctionDeclaration to OpenAI tool dict."""
        model = _model()
        fd = FunctionDeclaration(name="greet", description="Say hello")
        tool = Tool(function_declarations=[fd])
        result = model._convert_tools_to_openai_format([tool])
        assert len(result) == 1
        assert result[0]["function"]["name"] == "greet"
        assert result[0]["function"]["parameters"] == {"type": "object", "properties": {}}

    def test_with_parameters_schema(self):
        """Converts parameters schema correctly."""
        model = _model()
        schema = Schema(
            type=Type.OBJECT,
            properties={"city": Schema(type=Type.STRING, description="City name")},
            required=["city"],
        )
        fd = FunctionDeclaration(name="weather", description="Get weather", parameters=schema)
        tool = Tool(function_declarations=[fd])
        result = model._convert_tools_to_openai_format([tool])
        params = result[0]["function"]["parameters"]
        assert params["type"] == "object"
        assert "city" in params["properties"]
        assert params["required"] == ["city"]


# ---------------------------------------------------------------------------
# _convert_schema_to_openai_format
# ---------------------------------------------------------------------------


class TestConvertSchemaToOpenAIFormat:
    """Tests for _convert_schema_to_openai_format."""

    def test_empty_schema(self):
        """None schema returns default object structure."""
        model = _model()
        assert model._convert_schema_to_openai_format(None) == {"type": "object", "properties": {}}

    def test_string_type(self):
        """String type schema converts correctly."""
        model = _model()
        schema = Schema(type=Type.STRING, description="A name")
        result = model._convert_schema_to_openai_format(schema)
        assert result["type"] == "string"
        assert result["description"] == "A name"

    def test_array_type_with_items(self):
        """Array schema includes items sub-schema."""
        model = _model()
        schema = Schema(type=Type.ARRAY, items=Schema(type=Type.STRING))
        result = model._convert_schema_to_openai_format(schema)
        assert result["type"] == "array"
        assert result["items"]["type"] == "string"

    def test_object_type_without_explicit_type(self):
        """Schema without explicit type defaults to object."""
        model = _model()
        schema = Schema(properties={"x": Schema(type=Type.INTEGER)})
        result = model._convert_schema_to_openai_format(schema)
        assert result["type"] == "object"
        assert "x" in result["properties"]


# ---------------------------------------------------------------------------
# _ensure_additional_properties_false
# ---------------------------------------------------------------------------


class TestEnsureAdditionalPropertiesFalse:
    """Tests for _ensure_additional_properties_false."""

    def test_gpt_model_adds_field(self):
        """GPT model adds additionalProperties: false to object schemas."""
        model = _model(model_name="gpt-4")
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        result = model._ensure_additional_properties_false(schema)
        assert result["additionalProperties"] is False

    def test_non_gpt_model_returns_unchanged(self):
        """Non-GPT model returns schema unchanged."""
        model = _model(model_name="deepseek-chat")
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        result = model._ensure_additional_properties_false(schema)
        assert "additionalProperties" not in result

    def test_nested_objects(self):
        """Recursively processes nested object schemas for GPT models."""
        model = _model(model_name="gpt-4")
        schema = {
            "type": "object",
            "properties": {
                "inner": {"type": "object", "properties": {"b": {"type": "number"}}},
            },
        }
        result = model._ensure_additional_properties_false(schema)
        assert result["properties"]["inner"]["additionalProperties"] is False

    def test_non_dict_passthrough(self):
        """Non-dict values are passed through unchanged."""
        model = _model(model_name="gpt-4")
        assert model._ensure_additional_properties_false("not_a_dict") == "not_a_dict"


# ---------------------------------------------------------------------------
# _build_response_format
# ---------------------------------------------------------------------------


class TestBuildResponseFormat:
    """Tests for _build_response_format."""

    def test_json_mode_no_schema(self):
        """JSON mime type without schema returns json_object format."""
        model = _model()
        config = GenerateContentConfig(response_mime_type="application/json")
        result = model._build_response_format(config)
        assert result == {"type": "json_object"}

    def test_json_mode_with_json_schema(self):
        """JSON mime type with response_json_schema returns json_schema format."""
        model = _model()
        raw_schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        config = GenerateContentConfig(response_mime_type="application/json", response_json_schema=raw_schema)
        result = model._build_response_format(config)
        assert result is not None
        assert result["type"] == "json_schema"

    def test_no_json_mime_type(self):
        """Non-JSON mime type returns None."""
        model = _model()
        config = GenerateContentConfig(response_mime_type="text/plain")
        assert model._build_response_format(config) is None


# ---------------------------------------------------------------------------
# _extract_http_options
# ---------------------------------------------------------------------------


class TestExtractHttpOptions:
    """Tests for _extract_http_options."""

    def test_no_http_options(self):
        """Returns empty dict when no http_options set."""
        model = _model()
        config = GenerateContentConfig()
        assert model._extract_http_options(config) == {}

    def test_with_timeout(self):
        """Converts millisecond timeout to seconds."""
        model = _model()
        config = MagicMock()
        config.http_options.headers = None
        config.http_options.timeout = 5000
        config.http_options.extra_body = None
        result = model._extract_http_options(config)
        assert result["timeout"] == 5.0

    def test_with_callable_headers(self):
        """Callable header values are invoked."""
        model = _model()
        config = MagicMock()
        config.http_options.headers = {"X-Token": lambda: "dynamic_token"}
        config.http_options.timeout = None
        config.http_options.extra_body = None
        result = model._extract_http_options(config)
        assert result["extra_headers"]["X-Token"] == "dynamic_token"

    def test_with_extra_body(self):
        """Extra body is passed through."""
        model = _model()
        config = MagicMock()
        config.http_options.headers = None
        config.http_options.timeout = None
        config.http_options.extra_body = {"custom_key": "custom_val"}
        result = model._extract_http_options(config)
        assert result["extra_body"]["custom_key"] == "custom_val"


# ---------------------------------------------------------------------------
# _merge_configs
# ---------------------------------------------------------------------------


class TestMergeConfigs:
    """Tests for _merge_configs."""

    def test_no_request_config_no_default(self):
        """Returns empty config when both are None."""
        model = _model()
        result = model._merge_configs(None)
        assert isinstance(result, GenerateContentConfig)

    def test_no_request_config_uses_default(self):
        """Uses default config when request config is None."""
        default = GenerateContentConfig(temperature=0.5)
        model = _model(generate_content_config=default)
        result = model._merge_configs(None)
        assert result.temperature == 0.5

    def test_request_config_overrides_default(self):
        """Request config values override default config."""
        default = GenerateContentConfig(temperature=0.5, top_p=0.9)
        model = _model(generate_content_config=default)
        req_cfg = GenerateContentConfig(temperature=0.2)
        result = model._merge_configs(req_cfg)
        assert result.temperature == 0.2
        assert result.top_p == 0.9

    def test_no_default_returns_request_config(self):
        """Without default, returns request config as-is."""
        model = _model()
        req_cfg = GenerateContentConfig(temperature=0.3)
        result = model._merge_configs(req_cfg)
        assert result.temperature == 0.3


# ---------------------------------------------------------------------------
# _set_thinking
# ---------------------------------------------------------------------------


class TestSetThinking:
    """Tests for _set_thinking."""

    def test_no_thinking_config(self):
        """Noop when thinking_config is absent."""
        model = _model()
        request = _request([Content(parts=[Part.from_text(text="hi")], role="user")])
        http_options: dict = {}
        model._set_thinking(request, http_options)
        assert "extra_body" not in http_options

    def test_thinking_disabled(self):
        """Noop when include_thoughts is False."""
        model = _model()
        config = GenerateContentConfig(thinking_config=ThinkingConfig(include_thoughts=False))
        request = _request([Content(parts=[Part.from_text(text="hi")], role="user")], config=config)
        http_options: dict = {}
        model._set_thinking(request, http_options)
        assert "extra_body" not in http_options

    def test_thinking_budget_zero_disables(self):
        """Budget 0 sets thinking_enabled to False."""
        model = _model()
        config = GenerateContentConfig(
            thinking_config=ThinkingConfig(include_thoughts=True, thinking_budget=0),
            max_output_tokens=1000,
        )
        request = _request([Content(parts=[Part.from_text(text="hi")], role="user")], config=config)
        http_options: dict = {}
        model._set_thinking(request, http_options)
        assert http_options["extra_body"]["thinking_enabled"] is False

    def test_thinking_budget_auto(self):
        """Budget -1 enables thinking without setting tokens."""
        model = _model()
        config = GenerateContentConfig(
            thinking_config=ThinkingConfig(include_thoughts=True, thinking_budget=-1),
            max_output_tokens=2000,
        )
        request = _request([Content(parts=[Part.from_text(text="hi")], role="user")], config=config)
        http_options: dict = {}
        model._set_thinking(request, http_options)
        assert http_options["extra_body"]["thinking_enabled"] is True
        assert "thinking_tokens" not in http_options["extra_body"]

    def test_thinking_budget_positive(self):
        """Positive budget sets thinking_tokens."""
        model = _model()
        config = GenerateContentConfig(
            thinking_config=ThinkingConfig(include_thoughts=True, thinking_budget=500),
            max_output_tokens=2000,
        )
        request = _request([Content(parts=[Part.from_text(text="hi")], role="user")], config=config)
        http_options: dict = {}
        model._set_thinking(request, http_options)
        assert http_options["extra_body"]["thinking_tokens"] == 500

    def test_thinking_budget_exceeds_max_tokens_raises(self):
        """Budget exceeding max_output_tokens raises ValueError."""
        model = _model()
        config = GenerateContentConfig(
            thinking_config=ThinkingConfig(include_thoughts=True, thinking_budget=5000),
            max_output_tokens=2000,
        )
        request = _request([Content(parts=[Part.from_text(text="hi")], role="user")], config=config)
        with pytest.raises(ValueError, match="must be between"):
            model._set_thinking(request, {})

    def test_thinking_no_max_output_tokens_raises(self):
        """Missing max_output_tokens with budget raises ValueError."""
        model = _model()
        config = GenerateContentConfig(
            thinking_config=ThinkingConfig(include_thoughts=True, thinking_budget=500),
        )
        request = _request([Content(parts=[Part.from_text(text="hi")], role="user")], config=config)
        with pytest.raises(ValueError, match="max_output_tokens must be set"):
            model._set_thinking(request, {})

    def test_thinking_budget_negative_invalid(self):
        """Negative budget other than -1 raises ValueError."""
        model = _model()
        config = GenerateContentConfig(
            thinking_config=ThinkingConfig(include_thoughts=True, thinking_budget=-5),
            max_output_tokens=2000,
        )
        request = _request([Content(parts=[Part.from_text(text="hi")], role="user")], config=config)
        with pytest.raises(ValueError, match="Invalid thinking_budget value"):
            model._set_thinking(request, {})


# ---------------------------------------------------------------------------
# _format_messages
# ---------------------------------------------------------------------------


class TestFormatMessages:
    """Tests for _format_messages."""

    def test_simple_user_message(self):
        """Single user text produces one user message."""
        model = _model()
        content = Content(parts=[Part.from_text(text="hi")], role="user")
        config = GenerateContentConfig()
        request = _request([content], config=config)
        msgs = model._format_messages(request)
        assert any(m["role"] == "user" and m["content"] == "hi" for m in msgs)

    def test_model_role_mapped_to_assistant(self):
        """Content with role='model' becomes 'assistant' in output."""
        model = _model()
        content = Content(parts=[Part.from_text(text="reply")], role="model")
        config = GenerateContentConfig()
        request = _request([content], config=config)
        msgs = model._format_messages(request)
        assert msgs[-1]["role"] == "assistant"

    def test_system_instruction_prepended(self):
        """System instruction appears as the first message."""
        model = _model()
        content = Content(parts=[Part.from_text(text="q")], role="user")
        config = GenerateContentConfig(system_instruction="Be concise.")
        request = _request([content], config=config)
        msgs = model._format_messages(request)
        assert msgs[0]["role"] == "system"
        assert "Be concise" in msgs[0]["content"]

    def test_function_response_as_tool_message(self):
        """Function responses become tool role messages."""
        model = _model()
        fr = FunctionResponse(name="search", response={"result": "ok"})
        fr.id = "call_123"
        part = Part(function_response=fr)
        # Need an assistant message with tool call first, then tool response
        fc_part = Part.from_function_call(name="search", args={"q": "test"})
        fc_part.function_call.id = "call_123"
        assistant_content = Content(parts=[fc_part], role="model")
        tool_content = Content(parts=[part], role="user")
        config = GenerateContentConfig()
        request = _request([assistant_content, tool_content], config=config)
        msgs = model._format_messages(request)
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        assert len(tool_msgs) >= 1

    def test_multiple_text_parts_joined(self):
        """Multiple text parts in one content are joined with space."""
        model = _model()
        parts = [Part.from_text(text="hello"), Part.from_text(text="world")]
        content = Content(parts=parts, role="user")
        config = GenerateContentConfig()
        request = _request([content], config=config)
        msgs = model._format_messages(request)
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        assert any("hello world" in str(m.get("content", "")) for m in user_msgs)


# ---------------------------------------------------------------------------
# _validate_and_fix_openai_messages
# ---------------------------------------------------------------------------


class TestValidateAndFixOpenAIMessages:
    """Tests for _validate_and_fix_openai_messages."""

    def test_empty_messages(self):
        """Empty list returned as-is."""
        model = _model()
        assert model._validate_and_fix_openai_messages([]) == []

    def test_adds_dummy_tool_responses_for_pending_calls(self):
        """Adds dummy tool responses when tool calls are pending before a user message."""
        model = _model()
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
            },
            {"role": "user", "content": "next question"},
        ]
        fixed = model._validate_and_fix_openai_messages(messages)
        tool_msgs = [m for m in fixed if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "call_1"

    def test_matching_tool_response_clears_pending(self):
        """A matching tool response removes the pending call."""
        model = _model()
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "result"},
            {"role": "user", "content": "thanks"},
        ]
        fixed = model._validate_and_fix_openai_messages(messages)
        dummy_msgs = [m for m in fixed if m.get("role") == "tool" and "completed by system" in m.get("content", "")]
        assert len(dummy_msgs) == 0

    def test_remaining_pending_calls_at_end(self):
        """Pending tool calls at the end of messages get dummy responses."""
        model = _model()
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_x", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
            },
        ]
        fixed = model._validate_and_fix_openai_messages(messages)
        assert any(m.get("tool_call_id") == "call_x" for m in fixed)


# ---------------------------------------------------------------------------
# _get_part_thought_signature / _set_part_thought_signature
# ---------------------------------------------------------------------------


class TestThoughtSignatureHelpers:
    """Tests for thought signature get/set helpers."""

    def test_get_missing_returns_dummy(self):
        """Missing thought_signature returns base64-encoded dummy."""
        model = _model()
        part = Part.from_text(text="hi")
        sig = model._get_part_thought_signature(part)
        assert sig == base64.b64encode(b"skip_thought_signature_validator").decode("utf-8")

    def test_get_bytes_encodes(self):
        """Bytes thought_signature is base64 encoded."""
        model = _model()
        part = Part.from_text(text="hi")
        part.thought_signature = b"raw_sig"
        sig = model._get_part_thought_signature(part)
        assert sig == base64.b64encode(b"raw_sig").decode("utf-8")

    def test_get_string_passthrough(self):
        """String thought_signature returned as-is."""
        model = _model()
        part = Part.from_text(text="hi")
        part.thought_signature = "already_encoded"
        sig = model._get_part_thought_signature(part)
        assert sig == "already_encoded"

    def test_set_none_noop(self):
        """Setting None thought_signature does nothing."""
        model = _model()
        part = Part.from_function_call(name="f", args={})
        model._set_part_thought_signature(part, None)
        assert not hasattr(part, "_thought_signature_set")

    def test_set_valid_base64(self):
        """Setting valid base64 string decodes to bytes on part."""
        model = _model()
        part = Part.from_function_call(name="f", args={})
        encoded = base64.b64encode(b"test_sig").decode("utf-8")
        model._set_part_thought_signature(part, encoded)
        assert getattr(part, "thought_signature") == b"test_sig"


# ---------------------------------------------------------------------------
# _process_chunk_without_content
# ---------------------------------------------------------------------------


class TestProcessChunkWithoutContent:
    """Tests for _process_chunk_without_content."""

    def test_finish_reason_extracted(self):
        """Finish reason is extracted from choice."""
        model = _model()
        chunk = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        acc: list[dict] = []
        reason, usage, delta_args = model._process_chunk_without_content(chunk, acc)
        assert reason == FinishReason.STOP
        assert usage is None

    def test_tool_call_deltas_processed(self):
        """Tool call deltas are accumulated and delta_arguments returned."""
        model = _model()
        chunk = {
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "id": "call_1",
                        "function": {"name": "f", "arguments": '{"a":'},
                    }]
                },
                "finish_reason": None,
            }]
        }
        acc: list[dict] = []
        reason, usage, delta_args = model._process_chunk_without_content(chunk, acc)
        assert 0 in delta_args
        assert delta_args[0] == '{"a":'
        assert len(acc) == 1

    def test_choices_none_returns_usage_only(self):
        """Choices=None returns only usage data."""
        model = _model()
        chunk = {"choices": None, "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}
        acc: list[dict] = []
        reason, usage, delta_args = model._process_chunk_without_content(chunk, acc)
        assert reason is None
        assert usage is not None
        assert usage.prompt_token_count == 1

    def test_delta_none(self):
        """Delta=None returns None for all."""
        model = _model()
        chunk = {"choices": [{"delta": None}]}
        acc: list[dict] = []
        reason, usage, delta_args = model._process_chunk_without_content(chunk, acc)
        assert reason is None
        assert usage is None


# ---------------------------------------------------------------------------
# _create_streaming_tool_call_response
# ---------------------------------------------------------------------------


class TestCreateStreamingToolCallResponse:
    """Tests for _create_streaming_tool_call_response."""

    def test_empty_accumulated_returns_none(self):
        """Returns None for empty accumulated tool calls."""
        model = _model()
        assert model._create_streaming_tool_call_response([]) is None

    def test_no_matching_delta_returns_none(self):
        """Returns None when no delta_arguments match."""
        model = _model()
        acc = [{
            ToolKey.ID: "call_1",
            ToolKey.FUNCTION: {ToolKey.NAME: "f", ToolKey.ARGUMENTS: "{}"},
        }]
        result = model._create_streaming_tool_call_response(acc, delta_arguments={1: "x"})
        assert result is None

    def test_matching_delta_creates_response(self):
        """Matching delta produces a partial LlmResponse."""
        model = _model()
        acc = [{
            ToolKey.ID: "call_1",
            ToolKey.FUNCTION: {ToolKey.NAME: "search", ToolKey.ARGUMENTS: '{"q":"hi"}'},
        }]
        result = model._create_streaming_tool_call_response(
            acc,
            delta_arguments={0: '"hi"}'},
            streaming_tool_names={"search"},
        )
        assert result is not None
        assert result.partial is True

    def test_filtered_by_streaming_tool_names(self):
        """Tools not in streaming_tool_names are excluded."""
        model = _model()
        acc = [{
            ToolKey.ID: "call_1",
            ToolKey.FUNCTION: {ToolKey.NAME: "other_tool", ToolKey.ARGUMENTS: "{}"},
        }]
        result = model._create_streaming_tool_call_response(
            acc,
            delta_arguments={0: "x"},
            streaming_tool_names={"search"},
        )
        assert result is None


# ---------------------------------------------------------------------------
# _log_unsupported_config_options
# ---------------------------------------------------------------------------


class TestLogUnsupportedConfigOptions:
    """Tests for _log_unsupported_config_options."""

    def test_logs_warning_for_unsupported(self):
        """Warning is logged when unsupported options are set."""
        model = _model()
        config = GenerateContentConfig(top_k=40)
        with patch("trpc_agent_sdk.models._openai_model.logger") as mock_logger:
            model._log_unsupported_config_options(config)
            mock_logger.warning.assert_called_once()
            assert "top_k" in mock_logger.warning.call_args[0][1]

    def test_no_warning_for_supported(self):
        """No warning when only supported options are used."""
        model = _model()
        config = GenerateContentConfig(temperature=0.5)
        with patch("trpc_agent_sdk.models._openai_model.logger") as mock_logger:
            model._log_unsupported_config_options(config)
            mock_logger.warning.assert_not_called()


# ---------------------------------------------------------------------------
# generate_async edge cases
# ---------------------------------------------------------------------------


class TestGenerateAsyncEdgeCases:
    """Edge case tests for _generate_async_impl via generate_async."""

    @pytest.mark.asyncio
    async def test_streaming_error_yields_error_response(self):
        """Streaming errors yield an error LlmResponse."""
        model = _model()
        content = Content(parts=[Part.from_text(text="hi")], role="user")
        request = _request([content])

        with patch.object(model, "_create_async_client") as mock_factory:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(side_effect=Exception("stream fail"))
            mock_client.close = AsyncMock()
            mock_factory.return_value = mock_client

            responses = []
            async for r in model.generate_async(request, stream=True):
                responses.append(r)

            assert len(responses) == 1
            assert responses[0].error_code == "STREAMING_ERROR"

    @pytest.mark.asyncio
    async def test_non_streaming_with_additional_config_params(self):
        """Additional config params (frequency_penalty etc.) are passed through."""
        model = _model()
        content = Content(parts=[Part.from_text(text="hi")], role="user")
        config = GenerateContentConfig(
            max_output_tokens=100,
            frequency_penalty=0.5,
            presence_penalty=0.3,
            seed=42,
            candidate_count=2,
        )
        request = _request([content], config=config)

        mock_response = Mock()
        mock_response.model_dump.return_value = {
            "choices": [{"message": {"content": "ok", "role": "assistant"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

        captured = {}

        async def capture_create(**kwargs):
            captured.update(kwargs)
            return mock_response

        with patch.object(model, "_create_async_client") as mock_factory:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = capture_create
            mock_client.close = AsyncMock()
            mock_factory.return_value = mock_client

            async for _ in model.generate_async(request, stream=False):
                pass

        assert captured[ApiParamsKey.FREQUENCY_PENALTY] == 0.5
        assert captured[ApiParamsKey.PRESENCE_PENALTY] == 0.3
        assert captured[ApiParamsKey.SEED] == 42
        assert captured[ApiParamsKey.N] == 2

    @pytest.mark.asyncio
    async def test_streaming_with_thinking_content(self):
        """Streaming mode correctly tags reasoning_content as thought."""
        model = _model()
        content = Content(parts=[Part.from_text(text="think hard")], role="user")
        request = _request([content])

        chunk = Mock()
        chunk.model_dump.return_value = {
            "id": "resp_1",
            "choices": [{"delta": {"reasoning_content": "Let me think..."}, "finish_reason": None}],
            "usage": None,
        }
        final_chunk = Mock()
        final_chunk.model_dump.return_value = {
            "id": "resp_1",
            "choices": [{"delta": {"content": "The answer is 42"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
        }

        async def mock_stream():
            yield chunk
            yield final_chunk

        with patch.object(model, "_create_async_client") as mock_factory:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_stream())
            mock_client.close = AsyncMock()
            mock_factory.return_value = mock_client

            responses = []
            async for r in model.generate_async(request, stream=True):
                responses.append(r)

        partial_responses = [r for r in responses if r.partial]
        thought_partials = [r for r in partial_responses if r.content and r.content.parts[0].thought]
        assert len(thought_partials) >= 1

    @pytest.mark.asyncio
    async def test_streaming_null_response_raises(self):
        """Null response from API raises ValueError wrapped in error response."""
        model = _model()
        content = Content(parts=[Part.from_text(text="hi")], role="user")
        request = _request([content])

        with patch.object(model, "_create_async_client") as mock_factory:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=None)
            mock_client.close = AsyncMock()
            mock_factory.return_value = mock_client

            responses = []
            async for r in model.generate_async(request, stream=True):
                responses.append(r)

            assert any(r.error_code == "STREAMING_ERROR" for r in responses)

    @pytest.mark.asyncio
    async def test_add_tools_to_prompt_function_response_as_user(self):
        """With add_tools_to_prompt, function responses appear as user messages."""
        model = _model(add_tools_to_prompt=True)
        fr = FunctionResponse(name="search", response={"result": "ok"})
        part = Part(function_response=fr)
        tool_content = Content(parts=[part], role="user")
        user_content = Content(parts=[Part.from_text(text="done")], role="user")
        config = GenerateContentConfig()
        request = _request([tool_content, user_content], config=config)
        msgs = model._format_messages(request)
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        assert any("invoke search" in str(m.get("content", "")) for m in user_msgs)


# ---------------------------------------------------------------------------
# init edge cases
# ---------------------------------------------------------------------------


class TestInitEdgeCases:
    """Edge cases for __init__."""

    def test_invalid_tool_prompt_type_raises(self):
        """Non-string, non-ToolPrompt type raises ValueError."""
        with pytest.raises(ValueError, match="tool_prompt must be a string or ToolPrompt class"):
            OpenAIModel(model_name="gpt-4", api_key="key", tool_prompt=12345)

    def test_with_generate_content_config(self):
        """Default generate_content_config is stored."""
        cfg = GenerateContentConfig(temperature=0.1)
        model = _model(generate_content_config=cfg)
        assert model.generate_content_config.temperature == 0.1

    def test_organization_stored(self):
        """Organization kwarg is stored."""
        model = _model(organization="org-123")
        assert model.organization == "org-123"
