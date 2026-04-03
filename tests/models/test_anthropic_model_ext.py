# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Extended tests for Anthropic model — covers helper methods, edge cases, and streaming paths."""

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic import types as anthropic_types
from google.genai.types import Blob, CodeExecutionResult, ExecutableCode

from trpc_agent_sdk.models import AnthropicModel, LlmRequest, LlmResponse
from trpc_agent_sdk.models._anthropic_model import _FinishReason
from trpc_agent_sdk.types import (
    Content,
    FunctionDeclaration,
    GenerateContentConfig,
    Part,
    Schema,
    Tool,
    Type,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _model(**kwargs):
    defaults = dict(model_name="claude-3-5-sonnet-20241022", api_key="test-key")
    defaults.update(kwargs)
    return AnthropicModel(**defaults)


# ---------------------------------------------------------------------------
# _to_claude_role
# ---------------------------------------------------------------------------


class TestToClaudeRole:
    def test_model_role_maps_to_assistant(self):
        """'model' role is converted to 'assistant'."""
        model = _model()
        assert model._to_claude_role("model") == "assistant"

    def test_assistant_role_stays_assistant(self):
        """'assistant' role stays 'assistant'."""
        model = _model()
        assert model._to_claude_role("assistant") == "assistant"

    def test_user_role_stays_user(self):
        """'user' role stays 'user'."""
        model = _model()
        assert model._to_claude_role("user") == "user"

    def test_none_role_defaults_to_user(self):
        """None role defaults to 'user'."""
        model = _model()
        assert model._to_claude_role(None) == "user"

    def test_unknown_role_defaults_to_user(self):
        """Unknown role defaults to 'user'."""
        model = _model()
        assert model._to_claude_role("system") == "user"


# ---------------------------------------------------------------------------
# _is_image_part
# ---------------------------------------------------------------------------


class TestIsImagePart:
    def test_image_part_detected(self):
        """Image inline data is recognised as image."""
        model = _model()
        part = Part(inline_data=Blob(mime_type="image/png", data=b"abc"))
        assert model._is_image_part(part) is True

    def test_non_image_inline_data(self):
        """Non-image MIME type is not an image part."""
        model = _model()
        part = Part(inline_data=Blob(mime_type="application/pdf", data=b"abc"))
        assert model._is_image_part(part) is False

    def test_text_part_not_image(self):
        """Plain text part is not an image."""
        model = _model()
        part = Part.from_text(text="hello")
        assert not model._is_image_part(part)


# ---------------------------------------------------------------------------
# _part_to_message_block
# ---------------------------------------------------------------------------


class TestPartToMessageBlock:
    def test_text_part(self):
        """Text part converts to TextBlockParam."""
        model = _model()
        part = Part.from_text(text="hello")
        block = model._part_to_message_block(part)
        assert block["type"] == "text"
        assert block["text"] == "hello"

    def test_function_call_part(self):
        """Function call part converts to ToolUseBlockParam."""
        model = _model()
        part = Part.from_function_call(name="fn", args={"x": 1})
        part.function_call.id = "id1"
        block = model._part_to_message_block(part)
        assert block["type"] == "tool_use"
        assert block["name"] == "fn"
        assert block["input"] == {"x": 1}

    def test_function_response_with_content_array(self):
        """Function response with content array is serialised properly."""
        model = _model()
        part = Part.from_function_response(
            name="fn",
            response={"content": [{"type": "text", "text": "ok"}, {"type": "other", "data": 1}]},
        )
        part.function_response.id = "id1"
        block = model._part_to_message_block(part)
        assert block["type"] == "tool_result"
        assert "ok" in block["content"]

    def test_function_response_with_result_field(self):
        """Function response with 'result' field uses that value."""
        model = _model()
        part = Part.from_function_response(name="fn", response={"result": "done"})
        part.function_response.id = "id1"
        block = model._part_to_message_block(part)
        assert block["content"] == "done"

    def test_function_response_simple_json(self):
        """Function response fallback serialises the full dict."""
        model = _model()
        part = Part.from_function_response(name="fn", response={"key": "val"})
        part.function_response.id = "id1"
        block = model._part_to_message_block(part)
        assert json.loads(block["content"]) == {"key": "val"}

    def test_executable_code_part(self):
        """Executable code is wrapped in code block."""
        model = _model()
        part = Part(executable_code=ExecutableCode(code="print(1)", language="PYTHON"))
        block = model._part_to_message_block(part)
        assert block["type"] == "text"
        assert "print(1)" in block["text"]

    def test_code_execution_result_part(self):
        """Code execution result is wrapped in output block."""
        model = _model()
        part = Part(code_execution_result=CodeExecutionResult(output="42", outcome="OUTCOME_OK"))
        block = model._part_to_message_block(part)
        assert "42" in block["text"]

    def test_unsupported_part_raises(self):
        """Unsupported part type raises NotImplementedError."""
        model = _model()
        part = Part()
        with pytest.raises(NotImplementedError):
            model._part_to_message_block(part)


# ---------------------------------------------------------------------------
# _parse_finish_reason
# ---------------------------------------------------------------------------


class TestParseFinishReason:
    def test_end_turn_maps_to_stop(self):
        """'end_turn' maps to STOP."""
        model = _model()
        assert model._parse_finish_reason("end_turn") == _FinishReason.STOP

    def test_stop_sequence_maps_to_stop(self):
        """'stop_sequence' maps to STOP."""
        model = _model()
        assert model._parse_finish_reason("stop_sequence") == _FinishReason.STOP

    def test_max_tokens(self):
        """'max_tokens' maps to MAX_TOKENS."""
        model = _model()
        assert model._parse_finish_reason("max_tokens") == _FinishReason.MAX_TOKENS

    def test_tool_use(self):
        """'tool_use' maps to TOOL_USE."""
        model = _model()
        assert model._parse_finish_reason("tool_use") == _FinishReason.TOOL_USE

    def test_unknown_maps_to_error(self):
        """Unknown reason maps to ERROR."""
        model = _model()
        assert model._parse_finish_reason("something_else") == _FinishReason.ERROR

    def test_none_maps_to_error(self):
        """None maps to ERROR."""
        model = _model()
        assert model._parse_finish_reason(None) == _FinishReason.ERROR


# ---------------------------------------------------------------------------
# _update_type_string
# ---------------------------------------------------------------------------


class TestUpdateTypeString:
    def test_lowercases_type_field(self):
        """Type field is lowered."""
        model = _model()
        d = {"type": "STRING"}
        model._update_type_string(d)
        assert d["type"] == "string"

    def test_recursive_items(self):
        """Nested items type is lowered."""
        model = _model()
        d = {"type": "ARRAY", "items": {"type": "STRING"}}
        model._update_type_string(d)
        assert d["items"]["type"] == "string"

    def test_recursive_properties_in_items(self):
        """Properties inside items are recursively processed."""
        model = _model()
        d = {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING"},
                },
            },
        }
        model._update_type_string(d)
        assert d["items"]["properties"]["name"]["type"] == "string"


# ---------------------------------------------------------------------------
# _function_declaration_to_tool_param
# ---------------------------------------------------------------------------


class TestFunctionDeclarationToToolParam:
    def test_with_parameters_json_schema(self):
        """Uses parameters_json_schema when available."""
        model = _model()
        decl = MagicMock()
        decl.name = "my_fn"
        decl.description = "desc"
        decl.parameters_json_schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        result = model._function_declaration_to_tool_param(decl)
        assert result["name"] == "my_fn"
        assert result["input_schema"] == decl.parameters_json_schema

    def test_with_parameters_properties(self):
        """Converts from parameters when json_schema not available."""
        model = _model()
        decl = FunctionDeclaration(
            name="fn",
            description="d",
            parameters=Schema(
                type=Type.OBJECT,
                properties={"a": Schema(type=Type.STRING)},
                required=["a"],
            ),
        )
        result = model._function_declaration_to_tool_param(decl)
        assert result["name"] == "fn"
        assert "a" in result["input_schema"]["properties"]
        assert result["input_schema"]["required"] == ["a"]

    def test_no_parameters(self):
        """Works with no parameters at all."""
        model = _model()
        decl = FunctionDeclaration(name="fn", description="d")
        result = model._function_declaration_to_tool_param(decl)
        assert result["input_schema"]["type"] == "object"
        assert result["input_schema"]["properties"] == {}


# ---------------------------------------------------------------------------
# _content_block_to_part
# ---------------------------------------------------------------------------


class TestContentBlockToPart:
    def test_text_block(self):
        """TextBlock converts to text Part."""
        model = _model()
        block = anthropic_types.TextBlock(text="hello", type="text")
        part = model._content_block_to_part(block)
        assert part.text == "hello"

    def test_tool_use_block(self):
        """ToolUseBlock converts to function call Part."""
        model = _model()
        block = anthropic_types.ToolUseBlock(id="id1", name="fn", input={"a": 1}, type="tool_use")
        part = model._content_block_to_part(block)
        assert part.function_call.name == "fn"
        assert part.function_call.args == {"a": 1}
        assert part.function_call.id == "id1"

    def test_thinking_block(self):
        """ThinkingBlock converts to Part with thought=True."""
        model = _model()
        block = anthropic_types.ThinkingBlock(thinking="deep thoughts", type="thinking", signature="sig")
        part = model._content_block_to_part(block)
        assert part.text == "deep thoughts"
        assert part.thought is True

    def test_redacted_thinking_block(self):
        """RedactedThinkingBlock converts to Part with thought=True."""
        model = _model()
        block = anthropic_types.RedactedThinkingBlock(type="redacted_thinking", data="data")
        part = model._content_block_to_part(block)
        assert "redacted" in part.text.lower()
        assert part.thought is True

    def test_unsupported_block_raises(self):
        """Unsupported block raises NotImplementedError."""
        model = _model()
        block = MagicMock(spec=[])
        with pytest.raises(NotImplementedError):
            model._content_block_to_part(block)


# ---------------------------------------------------------------------------
# _format_messages — edge cases
# ---------------------------------------------------------------------------


class TestFormatMessagesEdgeCases:
    def test_image_in_assistant_turn_skipped(self):
        """Image data in assistant turn is skipped with warning."""
        model = _model()
        img_part = Part(inline_data=Blob(mime_type="image/png", data=b"\x89PNG"))
        request = LlmRequest(contents=[Content(parts=[img_part], role="model")])
        messages = model._format_messages(request)
        assert messages == []

    def test_empty_message_blocks_filtered(self):
        """Content that produces no blocks is not added to messages."""
        model = _model()
        img_part = Part(inline_data=Blob(mime_type="image/jpeg", data=b"\xff"))
        request = LlmRequest(contents=[Content(parts=[img_part], role="assistant")])
        messages = model._format_messages(request)
        assert messages == []


# ---------------------------------------------------------------------------
# _create_streaming_tool_call_response
# ---------------------------------------------------------------------------


class TestCreateStreamingToolCallResponse:
    def test_returns_none_when_no_tool_uses(self):
        """Returns None with empty accumulated_tool_uses."""
        model = _model()
        assert model._create_streaming_tool_call_response([], '{"a":1}') is None

    def test_returns_none_when_tool_name_empty(self):
        """Returns None when current tool has no name."""
        model = _model()
        result = model._create_streaming_tool_call_response(
            [{"id": "id1", "name": "", "accumulated_input": ""}],
            '{"x":1}',
        )
        assert result is None

    def test_returns_none_when_tool_not_in_streaming_set(self):
        """Returns None when tool name is not in streaming_tool_names."""
        model = _model()
        result = model._create_streaming_tool_call_response(
            [{"id": "id1", "name": "my_tool", "accumulated_input": "{}"}],
            '{"x":1}',
            streaming_tool_names={"other_tool"},
        )
        assert result is None

    def test_returns_response_when_tool_in_streaming_set(self):
        """Returns LlmResponse when tool name is in streaming_tool_names."""
        model = _model()
        result = model._create_streaming_tool_call_response(
            [{"id": "id1", "name": "my_tool", "accumulated_input": '{"x":'}],
            '1}',
            streaming_tool_names={"my_tool"},
        )
        assert result is not None
        assert result.partial is True
        assert result.content.parts[0].function_call.name == "my_tool"

    def test_returns_response_when_streaming_names_none(self):
        """Returns response for any tool when streaming_tool_names is None."""
        model = _model()
        result = model._create_streaming_tool_call_response(
            [{"id": "id1", "name": "any_tool", "accumulated_input": ""}],
            '{}',
            streaming_tool_names=None,
        )
        assert result is not None


# ---------------------------------------------------------------------------
# _merge_configs — additional cases
# ---------------------------------------------------------------------------


class TestMergeConfigsExtended:
    def test_no_request_no_default_returns_empty(self):
        """No request config and no default yields empty GenerateContentConfig."""
        model = _model()
        merged = model._merge_configs(None)
        assert isinstance(merged, GenerateContentConfig)

    def test_default_fields_applied_to_request(self):
        """Default config fields fill in unset request config fields."""
        default = GenerateContentConfig(temperature=0.3, top_p=0.9, max_output_tokens=500)
        model = _model(generate_content_config=default)
        req = GenerateContentConfig(temperature=0.7)
        merged = model._merge_configs(req)
        assert merged.temperature == 0.7
        assert merged.max_output_tokens == 500
        assert merged.top_p == 0.9


# ---------------------------------------------------------------------------
# _log_unsupported_config_options
# ---------------------------------------------------------------------------


class TestLogUnsupportedConfigOptions:
    def test_logs_warning_for_unsupported_options(self):
        """Logs warnings for every unsupported config option."""
        model = _model()
        config = GenerateContentConfig(
            frequency_penalty=0.5,
            presence_penalty=0.5,
            seed=42,
        )
        with patch("trpc_agent_sdk.models._anthropic_model.logger") as mock_logger:
            model._log_unsupported_config_options(config)
            mock_logger.warning.assert_called_once()
            logged_msg = mock_logger.warning.call_args[0][1]
            assert "frequency_penalty" in logged_msg
            assert "presence_penalty" in logged_msg
            assert "seed" in logged_msg

    def test_no_warning_for_supported_options(self):
        """No warning when only supported options are set."""
        model = _model()
        config = GenerateContentConfig(temperature=0.5)
        with patch("trpc_agent_sdk.models._anthropic_model.logger") as mock_logger:
            model._log_unsupported_config_options(config)
            mock_logger.warning.assert_not_called()


# ---------------------------------------------------------------------------
# _generate_single — error path
# ---------------------------------------------------------------------------


class TestGenerateSingleError:
    @pytest.mark.asyncio
    async def test_api_error_returns_error_response(self):
        """API error during single generation returns error LlmResponse."""
        model = _model()
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=RuntimeError("timeout"))
        mock_client.close = AsyncMock()
        with patch.object(model, "_create_async_client", return_value=mock_client):
            resp = await model._generate_single({}, LlmRequest(contents=[]))
            assert resp.error_code == "API_ERROR"
            assert "timeout" in resp.error_message
            mock_client.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# _create_async_client
# ---------------------------------------------------------------------------


class TestCreateAsyncClient:
    def test_client_created_with_api_key(self):
        """Client is created with the model's api_key."""
        model = _model(api_key="sk-test", base_url="https://example.com")
        with patch("trpc_agent_sdk.models._anthropic_model.AsyncAnthropic") as MockClient:
            model._create_async_client()
            MockClient.assert_called_once()
            kwargs = MockClient.call_args.kwargs
            assert kwargs["api_key"] == "sk-test"
            assert kwargs["base_url"] == "https://example.com"
            assert kwargs["max_retries"] == 0

    def test_client_without_base_url(self):
        """Client is created without base_url when not set."""
        model = _model(api_key="sk-test")
        with patch("trpc_agent_sdk.models._anthropic_model.AsyncAnthropic") as MockClient:
            model._create_async_client()
            kwargs = MockClient.call_args.kwargs
            assert kwargs["base_url"] is None


# ---------------------------------------------------------------------------
# validate_request — edge cases
# ---------------------------------------------------------------------------


class TestValidateRequestExtended:
    def test_part_with_inline_data_is_valid(self):
        """Part with inline_data passes validation."""
        model = _model()
        part = Part(inline_data=Blob(mime_type="image/png", data=b"img"))
        request = LlmRequest(contents=[Content(parts=[part], role="user")])
        model.validate_request(request)

    def test_part_with_no_content_raises(self):
        """Part with no meaningful content raises ValueError."""
        model = _model()
        part = Part()
        request = LlmRequest(contents=[Content(parts=[part], role="user")])
        with pytest.raises(ValueError, match="Content parts must have"):
            model.validate_request(request)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
