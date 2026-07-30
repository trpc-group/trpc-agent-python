# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.a2a.converters._part_converter."""

from __future__ import annotations

import base64
import json
from enum import Enum
from unittest.mock import MagicMock

import pytest
try:
    from a2a import types as a2a_types
    _ = a2a_types.DataPart
    _ = a2a_types.TextPart
except (ImportError, AttributeError):
    pytest.skip(
        "Installed a2a.types does not export DataPart/TextPart; skip legacy A2A tests.",
        allow_module_level=True,
    )
from google.genai import types as genai_types

from trpc_agent_sdk.models import TOOL_STREAMING_ARGS
from trpc_agent_sdk.server.a2a._constants import (
    A2A_DATA_FIELD_CODE_EXECUTION_CODE,
    A2A_DATA_FIELD_CODE_EXECUTION_LANGUAGE,
    A2A_DATA_FIELD_CODE_EXECUTION_OUTCOME,
    A2A_DATA_FIELD_CODE_EXECUTION_OUTPUT,
    A2A_DATA_FIELD_TOOL_CALL_ARGS,
    A2A_DATA_FIELD_TOOL_CALL_RESPONSE,
    A2A_DATA_PART_METADATA_TYPE_CODE_EXECUTION_RESULT,
    A2A_DATA_PART_METADATA_TYPE_EXECUTABLE_CODE,
    A2A_DATA_PART_METADATA_TYPE_FUNCTION_CALL,
    A2A_DATA_PART_METADATA_TYPE_FUNCTION_RESPONSE,
    A2A_DATA_PART_METADATA_TYPE_KEY,
    A2A_DATA_PART_METADATA_TYPE_STREAMING_FUNCTION_CALL_DELTA,
)
from trpc_agent_sdk.server.a2a.converters._part_converter import (
    _a2a_string_field,
    _convert_a2a_data_part,
    _convert_streaming_function_call_delta,
    _function_call_data_for_a2a,
    _function_response_data_for_a2a,
    _genai_code_execution_result_to_a2a,
    _genai_executable_code_to_a2a,
    _genai_file_uri_to_a2a,
    _genai_function_call_to_a2a,
    _genai_function_response_to_a2a,
    _genai_inline_file_to_a2a,
    _genai_streaming_function_call_to_a2a,
    _genai_text_to_a2a,
    _get_genai_part_kind,
    _normalize_function_call_data,
    _normalize_function_response_data,
    _stringify,
    _to_bool_metadata,
    _typed_metadata,
    convert_a2a_part_to_genai_part,
    convert_genai_part_to_a2a_part,
)


# ---------------------------------------------------------------------------
# _to_bool_metadata
# ---------------------------------------------------------------------------
class TestToBoolMetadata:
    def test_bool_true(self):
        assert _to_bool_metadata(True) is True

    def test_bool_false(self):
        assert _to_bool_metadata(False) is False

    def test_string_true(self):
        assert _to_bool_metadata("true") is True
        assert _to_bool_metadata("True") is True
        assert _to_bool_metadata("  TRUE  ") is True

    def test_string_false(self):
        assert _to_bool_metadata("false") is False
        assert _to_bool_metadata("  False  ") is False

    def test_non_bool_string(self):
        assert _to_bool_metadata("yes") is None

    def test_none(self):
        assert _to_bool_metadata(None) is None

    def test_integer(self):
        assert _to_bool_metadata(1) is None


# ---------------------------------------------------------------------------
# _stringify
# ---------------------------------------------------------------------------
class TestStringify:
    def test_none(self):
        assert _stringify(None) == ""

    def test_string(self):
        assert _stringify("hello") == "hello"

    def test_integer(self):
        assert _stringify(42) == "42"

    def test_enum(self):
        class Color(Enum):
            RED = "red"
        assert _stringify(Color.RED) == "red"


# ---------------------------------------------------------------------------
# _a2a_string_field
# ---------------------------------------------------------------------------
class TestA2aStringField:
    def test_none(self):
        assert _a2a_string_field(None) == ""

    def test_string(self):
        assert _a2a_string_field("hello") == "hello"

    def test_dict(self):
        result = _a2a_string_field({"a": 1})
        assert json.loads(result) == {"a": 1}

    def test_list(self):
        result = _a2a_string_field([1, 2])
        assert json.loads(result) == [1, 2]

    def test_number(self):
        assert _a2a_string_field(42) == "42"


# ---------------------------------------------------------------------------
# _typed_metadata
# ---------------------------------------------------------------------------
class TestTypedMetadata:
    def test_returns_dict_with_type(self):
        result = _typed_metadata("function_call")
        assert result == {A2A_DATA_PART_METADATA_TYPE_KEY: "function_call"}


# ---------------------------------------------------------------------------
# _get_genai_part_kind
# ---------------------------------------------------------------------------
class TestGetGenaiPartKind:
    def test_text(self):
        part = genai_types.Part(text="hi")
        assert _get_genai_part_kind(part) == "text"

    def test_file_data(self):
        part = genai_types.Part(file_data=genai_types.FileData(file_uri="gs://bucket/f", mime_type="text/plain"))
        assert _get_genai_part_kind(part) == "file_uri"

    def test_inline_data(self):
        part = genai_types.Part(inline_data=genai_types.Blob(data=b"abc", mime_type="text/plain"))
        assert _get_genai_part_kind(part) == "inline_file"

    def test_function_call(self):
        part = genai_types.Part(function_call=genai_types.FunctionCall(name="fn", args={"a": 1}))
        assert _get_genai_part_kind(part) == "function_call"

    def test_streaming_function_call(self):
        part = genai_types.Part(function_call=genai_types.FunctionCall(
            name="fn", args={TOOL_STREAMING_ARGS: "delta"}))
        assert _get_genai_part_kind(part) == "streaming_function_call"

    def test_function_response(self):
        part = genai_types.Part(function_response=genai_types.FunctionResponse(name="fn", response={"r": 1}))
        assert _get_genai_part_kind(part) == "function_response"

    def test_code_execution_result(self):
        part = genai_types.Part(code_execution_result=genai_types.CodeExecutionResult(output="out", outcome="OUTCOME_OK"))
        assert _get_genai_part_kind(part) == "code_execution_result"

    def test_executable_code(self):
        part = genai_types.Part(executable_code=genai_types.ExecutableCode(code="print(1)", language="PYTHON"))
        assert _get_genai_part_kind(part) == "executable_code"

    def test_unknown(self):
        part = genai_types.Part()
        assert _get_genai_part_kind(part) is None


# ---------------------------------------------------------------------------
# GenAI → A2A converters
# ---------------------------------------------------------------------------
class TestGenaiTextToA2a:
    def test_basic_text(self):
        part = genai_types.Part(text="hello")
        result = _genai_text_to_a2a(part)
        assert isinstance(result.root, a2a_types.TextPart)
        assert result.root.text == "hello"

    def test_text_with_thought(self):
        part = genai_types.Part(text="thinking...", thought=True)
        result = _genai_text_to_a2a(part)
        assert result.root.metadata == {"thought": True}

    def test_text_without_thought(self):
        part = genai_types.Part(text="no thought")
        result = _genai_text_to_a2a(part)
        assert result.root.metadata is None


class TestGenaiFileUriToA2a:
    def test_basic(self):
        part = genai_types.Part(file_data=genai_types.FileData(file_uri="gs://b/f", mime_type="image/png"))
        result = _genai_file_uri_to_a2a(part)
        assert isinstance(result.root, a2a_types.FilePart)
        assert isinstance(result.root.file, a2a_types.FileWithUri)
        assert result.root.file.uri == "gs://b/f"


class TestGenaiInlineFileToA2a:
    def test_basic(self):
        data = b"binary_data"
        part = genai_types.Part(inline_data=genai_types.Blob(data=data, mime_type="application/octet-stream"))
        result = _genai_inline_file_to_a2a(part)
        assert isinstance(result.root, a2a_types.FilePart)
        assert isinstance(result.root.file, a2a_types.FileWithBytes)
        assert base64.b64decode(result.root.file.bytes) == data


class TestGenaiStreamingFunctionCallToA2a:
    def test_basic(self):
        part = genai_types.Part(function_call=genai_types.FunctionCall(
            id="tool1", name="fn", args={TOOL_STREAMING_ARGS: "partial"}))
        result = _genai_streaming_function_call_to_a2a(part)
        assert isinstance(result.root, a2a_types.DataPart)
        assert result.root.data["name"] == "fn"
        assert result.root.data["delta_args"] == "partial"
        assert result.root.metadata[A2A_DATA_PART_METADATA_TYPE_KEY] == A2A_DATA_PART_METADATA_TYPE_STREAMING_FUNCTION_CALL_DELTA


class TestGenaiFunctionCallToA2a:
    def test_basic(self):
        part = genai_types.Part(function_call=genai_types.FunctionCall(name="fn", args={"x": 1}))
        result = _genai_function_call_to_a2a(part)
        assert isinstance(result.root, a2a_types.DataPart)
        assert result.root.metadata[A2A_DATA_PART_METADATA_TYPE_KEY] == A2A_DATA_PART_METADATA_TYPE_FUNCTION_CALL


class TestGenaiFunctionResponseToA2a:
    def test_basic(self):
        part = genai_types.Part(function_response=genai_types.FunctionResponse(name="fn", response={"r": "ok"}))
        result = _genai_function_response_to_a2a(part)
        assert isinstance(result.root, a2a_types.DataPart)
        assert result.root.metadata[A2A_DATA_PART_METADATA_TYPE_KEY] == A2A_DATA_PART_METADATA_TYPE_FUNCTION_RESPONSE


class TestGenaiCodeExecutionResultToA2a:
    def test_basic(self):
        part = genai_types.Part(code_execution_result=genai_types.CodeExecutionResult(output="result", outcome="OUTCOME_OK"))
        result = _genai_code_execution_result_to_a2a(part)
        assert isinstance(result.root, a2a_types.DataPart)
        assert result.root.data[A2A_DATA_FIELD_CODE_EXECUTION_OUTPUT] == "result"


class TestGenaiExecutableCodeToA2a:
    def test_basic(self):
        part = genai_types.Part(executable_code=genai_types.ExecutableCode(code="print(1)", language="PYTHON"))
        result = _genai_executable_code_to_a2a(part)
        assert isinstance(result.root, a2a_types.DataPart)
        assert result.root.data[A2A_DATA_FIELD_CODE_EXECUTION_CODE] == "print(1)"


# ---------------------------------------------------------------------------
# convert_genai_part_to_a2a_part (dispatch)
# ---------------------------------------------------------------------------
class TestConvertGenaiPartToA2aPart:
    def test_text_dispatch(self):
        part = genai_types.Part(text="hi")
        result = convert_genai_part_to_a2a_part(part)
        assert isinstance(result.root, a2a_types.TextPart)

    def test_unknown_returns_none(self):
        part = genai_types.Part()
        result = convert_genai_part_to_a2a_part(part)
        assert result is None


# ---------------------------------------------------------------------------
# A2A → GenAI helpers
# ---------------------------------------------------------------------------
class TestFunctionCallDataForA2a:
    def test_dict_input(self):
        result = _function_call_data_for_a2a({"name": "fn", "args": {"a": 1}})
        assert result["type"] == "function"
        assert result["args"] == '{"a": 1}'

    def test_non_dict_with_model_dump(self):
        obj = MagicMock()
        obj.model_dump.return_value = {"name": "fn", "args": {"a": 1}}
        result = _function_call_data_for_a2a(obj)
        assert result["type"] == "function"


class TestFunctionResponseDataForA2a:
    def test_dict_input(self):
        result = _function_response_data_for_a2a({"name": "fn", "response": {"r": 1}})
        assert result["response"] == '{"r": 1}'

    def test_non_dict_with_model_dump(self):
        obj = MagicMock()
        obj.model_dump.return_value = {"name": "fn", "response": "ok"}
        result = _function_response_data_for_a2a(obj)
        assert "name" in result


class TestNormalizeFunctionCallData:
    def test_parses_args_json(self):
        result = _normalize_function_call_data({"name": "fn", "args": '{"x": 1}', "type": "function"})
        assert result["args"] == {"x": 1}
        assert "type" not in result

    def test_invalid_json_args_fallback(self):
        result = _normalize_function_call_data({"args": "not json"})
        assert result["args"] == {}

    def test_non_dict_input(self):
        result = _normalize_function_call_data("not a dict")
        assert result == {}


class TestNormalizeFunctionResponseData:
    def test_parses_response_json(self):
        result = _normalize_function_response_data({"name": "fn", "response": '{"r": 1}'})
        assert result["response"] == {"r": 1}

    def test_invalid_json_wraps_in_content(self):
        result = _normalize_function_response_data({"response": "plain text"})
        assert result["response"] == {"content": "plain text"}

    def test_non_dict_input(self):
        result = _normalize_function_response_data("not a dict")
        assert result == {}


class TestConvertStreamingFunctionCallDelta:
    def test_basic(self):
        data = {"id": "t1", "name": "fn", "delta_args": "partial"}
        result = _convert_streaming_function_call_delta(data)
        assert result.function_call.name == "fn"
        assert result.function_call.args[TOOL_STREAMING_ARGS] == "partial"

    def test_none_data(self):
        result = _convert_streaming_function_call_delta(None)
        assert result.function_call is not None


# ---------------------------------------------------------------------------
# _convert_a2a_data_part
# ---------------------------------------------------------------------------
class TestConvertA2aDataPart:
    def test_function_call(self):
        dp = a2a_types.DataPart(
            data={"name": "fn", "args": '{"x": 1}'},
            metadata={A2A_DATA_PART_METADATA_TYPE_KEY: A2A_DATA_PART_METADATA_TYPE_FUNCTION_CALL},
        )
        result = _convert_a2a_data_part(dp)
        assert result.function_call is not None
        assert result.function_call.name == "fn"

    def test_unknown_type_falls_back_to_json_text(self):
        dp = a2a_types.DataPart(data={"custom": "value"}, metadata={"type": "unknown"})
        result = _convert_a2a_data_part(dp)
        assert result.text is not None
        parsed = json.loads(result.text)
        assert parsed["custom"] == "value"

    def test_no_metadata_type(self):
        dp = a2a_types.DataPart(data={"k": "v"})
        result = _convert_a2a_data_part(dp)
        assert result.text is not None


# ---------------------------------------------------------------------------
# convert_a2a_part_to_genai_part (dispatch)
# ---------------------------------------------------------------------------
class TestConvertA2aPartToGenaiPart:
    def test_text_part(self):
        a2a_part = a2a_types.Part(root=a2a_types.TextPart(text="hello"))
        result = convert_a2a_part_to_genai_part(a2a_part)
        assert result.text == "hello"

    def test_text_part_with_thought(self):
        tp = a2a_types.TextPart(text="thinking")
        tp.metadata = {"thought": "true"}
        a2a_part = a2a_types.Part(root=tp)
        result = convert_a2a_part_to_genai_part(a2a_part)
        assert result.text == "thinking"
        assert result.thought is True

    def test_file_with_uri(self):
        fp = a2a_types.FilePart(file=a2a_types.FileWithUri(uri="gs://b/f", mime_type="text/plain"))
        a2a_part = a2a_types.Part(root=fp)
        result = convert_a2a_part_to_genai_part(a2a_part)
        assert result.file_data.file_uri == "gs://b/f"

    def test_file_with_bytes(self):
        data = b"hello"
        fp = a2a_types.FilePart(file=a2a_types.FileWithBytes(
            bytes=base64.b64encode(data).decode("utf-8"),
            mime_type="text/plain",
        ))
        a2a_part = a2a_types.Part(root=fp)
        result = convert_a2a_part_to_genai_part(a2a_part)
        assert result.inline_data.data == data

    def test_data_part(self):
        dp = a2a_types.DataPart(
            data={"name": "fn", "args": "{}"},
            metadata={A2A_DATA_PART_METADATA_TYPE_KEY: A2A_DATA_PART_METADATA_TYPE_FUNCTION_CALL},
        )
        a2a_part = a2a_types.Part(root=dp)
        result = convert_a2a_part_to_genai_part(a2a_part)
        assert result.function_call is not None

    def test_unsupported_file_type_returns_none(self):
        mock_file_part = MagicMock(spec=a2a_types.FilePart)
        mock_file_part.file = MagicMock()
        a2a_part = MagicMock(spec=a2a_types.Part)
        a2a_part.root = mock_file_part
        result = convert_a2a_part_to_genai_part(a2a_part)
        assert result is None
