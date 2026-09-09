# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# Below code are copy and modified from https://github.com/google/adk-python.git
#
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Conversion between A2A Part and Google GenAI Part.

In a2a-sdk 1.x ``Part`` is a protobuf message with a oneof content field
(``text`` / ``raw`` / ``url`` / ``data``) instead of a pydantic wrapper around
``TextPart``/``FilePart``/``DataPart``.  This module converts between the
GenAI ``Part`` model and the protobuf ``Part``.
"""

from __future__ import annotations

import json
from typing import Any
from typing import Optional

from google.genai import types as genai_types
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict, ParseDict
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import TOOL_STREAMING_ARGS

from .._constants import A2A_DATA_FIELD_CODE_EXECUTION_CODE
from .._constants import A2A_DATA_FIELD_CODE_EXECUTION_LANGUAGE
from .._constants import A2A_DATA_FIELD_CODE_EXECUTION_OUTCOME
from .._constants import A2A_DATA_FIELD_CODE_EXECUTION_OUTPUT
from .._constants import A2A_DATA_FIELD_TOOL_CALL_ARGS
from .._constants import A2A_DATA_FIELD_TOOL_CALL_RESPONSE
from .._constants import A2A_DATA_PART_METADATA_TYPE_CODE_EXECUTION_RESULT
from .._constants import A2A_DATA_PART_METADATA_TYPE_EXECUTABLE_CODE
from .._constants import A2A_DATA_PART_METADATA_TYPE_FUNCTION_CALL
from .._constants import A2A_DATA_PART_METADATA_TYPE_FUNCTION_RESPONSE
from .._constants import A2A_DATA_PART_METADATA_TYPE_KEY
from .._constants import A2A_DATA_PART_METADATA_TYPE_STREAMING_FUNCTION_CALL_DELTA
from .._utils import get_metadata
from .._utils import has_field
from .._utils import set_metadata

_A2A_PART_MODULE = None


def _a2a_part_type() -> Any:
    """Lazily import the A2A Part type to avoid a hard dependency."""
    global _A2A_PART_MODULE  # pylint: disable=global-statement
    if _A2A_PART_MODULE is None:
        from a2a.types import Part as A2APart

        _A2A_PART_MODULE = A2APart
    return _A2A_PART_MODULE


def _to_bool_metadata(value: Any) -> Optional[bool]:
    """Convert metadata values to bool when possible."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lower = value.strip().lower()
        if lower == "true":
            return True
        if lower == "false":
            return False
    return None


def _stringify(value: Any) -> str:
    """Convert code-execution field values to strings, unwrapping enums."""
    if value is None:
        return ""
    if hasattr(value, "value"):
        value = value.value
    return str(value)


def _a2a_string_field(value: Any) -> str:
    """Serialize a DataPart field to A2A wire string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def _typed_metadata(type_value: str) -> dict[str, Any]:
    """Build metadata dict with a single ``type`` entry."""
    return {A2A_DATA_PART_METADATA_TYPE_KEY: type_value}


def _get_genai_part_kind(part: genai_types.Part) -> Optional[str]:
    """Return the logical kind for a GenAI part."""
    if part.text:
        return "text"
    if part.file_data:
        return "file_uri"
    if part.inline_data:
        return "inline_file"
    if part.function_call:
        args = part.function_call.args or {}
        if args.get(TOOL_STREAMING_ARGS) is not None:
            return "streaming_function_call"
        return "function_call"
    if part.function_response:
        return "function_response"
    if part.code_execution_result:
        return "code_execution_result"
    if part.executable_code:
        return "executable_code"
    return None


def _new_a2a_part(**kwargs: Any) -> Any:
    """Build a protobuf A2A Part with the given oneof fields.

    The ``data`` field is a ``google.protobuf.Value``; a plain dict must be
    wrapped via ``ParseDict`` before being passed to the constructor.
    """
    data = kwargs.pop("data", None)
    part = _a2a_part_type()(**kwargs)
    if data is not None:
        part.data.CopyFrom(ParseDict(data, struct_pb2.Value()))
    return part


def _genai_text_to_a2a(part: genai_types.Part) -> Optional[Any]:
    metadata = None
    if part.thought is not None:
        metadata = {"thought": part.thought}
    return _new_a2a_part(text=part.text, metadata=metadata)


def _genai_file_uri_to_a2a(part: genai_types.Part) -> Optional[Any]:
    return _new_a2a_part(
        url=part.file_data.file_uri,
        media_type=part.file_data.mime_type,
    )


def _genai_inline_file_to_a2a(part: genai_types.Part) -> Optional[Any]:
    metadata = None
    if part.video_metadata:
        metadata = {
            "video_metadata": part.video_metadata.model_dump(by_alias=True, exclude_none=True),
        }
    return _new_a2a_part(
        raw=part.inline_data.data,
        media_type=part.inline_data.mime_type,
        metadata=metadata,
    )


def _genai_streaming_function_call_to_a2a(part: genai_types.Part) -> Optional[Any]:
    fc = part.function_call
    tool_id = fc.id or f"tool_{fc.name}_{id(fc)}"
    data: dict[str, Any] = {
        "id": tool_id,
        "name": fc.name,
        "delta_args": (fc.args or {}).get(TOOL_STREAMING_ARGS),
    }
    metadata = _typed_metadata(A2A_DATA_PART_METADATA_TYPE_STREAMING_FUNCTION_CALL_DELTA)
    set_metadata(metadata, "streaming", True)
    return _new_a2a_part(data=data, metadata=metadata)


def _function_call_data_for_a2a(raw: Any) -> dict[str, Any]:
    """Build A2A DataPart data from function_call; args emitted as string, data.type=\"function\"."""
    if not isinstance(raw, dict):
        raw = raw.model_dump(by_alias=True, exclude_none=True) if hasattr(raw, "model_dump") else {}
    out = dict(raw)
    out.pop("type", None)
    if A2A_DATA_FIELD_TOOL_CALL_ARGS in out:
        out[A2A_DATA_FIELD_TOOL_CALL_ARGS] = _a2a_string_field(out[A2A_DATA_FIELD_TOOL_CALL_ARGS])
    out["type"] = "function"
    return out


def _genai_function_call_to_a2a(part: genai_types.Part) -> Optional[Any]:
    data = _function_call_data_for_a2a(part.function_call)
    return _new_a2a_part(
        data=data,
        metadata=_typed_metadata(A2A_DATA_PART_METADATA_TYPE_FUNCTION_CALL),
    )


def _function_response_data_for_a2a(raw: Any) -> dict[str, Any]:
    """Build A2A DataPart data from function_response; response emitted as string."""
    if not isinstance(raw, dict):
        raw = raw.model_dump(by_alias=True, exclude_none=True) if hasattr(raw, "model_dump") else {}
    out = dict(raw)
    if A2A_DATA_FIELD_TOOL_CALL_RESPONSE in out:
        out[A2A_DATA_FIELD_TOOL_CALL_RESPONSE] = _a2a_string_field(out[A2A_DATA_FIELD_TOOL_CALL_RESPONSE])
    return out


def _genai_function_response_to_a2a(part: genai_types.Part) -> Optional[Any]:
    data = _function_response_data_for_a2a(part.function_response)
    return _new_a2a_part(
        data=data,
        metadata=_typed_metadata(A2A_DATA_PART_METADATA_TYPE_FUNCTION_RESPONSE),
    )


def _genai_code_execution_result_to_a2a(part: genai_types.Part) -> Optional[Any]:
    return _new_a2a_part(
        data={
            A2A_DATA_FIELD_CODE_EXECUTION_OUTPUT: _stringify(part.code_execution_result.output),
            A2A_DATA_FIELD_CODE_EXECUTION_OUTCOME: _stringify(part.code_execution_result.outcome),
        },
        metadata=_typed_metadata(A2A_DATA_PART_METADATA_TYPE_CODE_EXECUTION_RESULT),
    )


def _genai_executable_code_to_a2a(part: genai_types.Part) -> Optional[Any]:
    return _new_a2a_part(
        data={
            A2A_DATA_FIELD_CODE_EXECUTION_CODE: _stringify(part.executable_code.code),
            A2A_DATA_FIELD_CODE_EXECUTION_LANGUAGE: _stringify(part.executable_code.language) or "unknown",
        },
        metadata=_typed_metadata(A2A_DATA_PART_METADATA_TYPE_EXECUTABLE_CODE),
    )


_GENAI_KIND_CONVERTERS: dict[str, callable] = {
    "text": _genai_text_to_a2a,
    "file_uri": _genai_file_uri_to_a2a,
    "inline_file": _genai_inline_file_to_a2a,
    "streaming_function_call": _genai_streaming_function_call_to_a2a,
    "function_call": _genai_function_call_to_a2a,
    "function_response": _genai_function_response_to_a2a,
    "code_execution_result": _genai_code_execution_result_to_a2a,
    "executable_code": _genai_executable_code_to_a2a,
}


def convert_genai_part_to_a2a_part(part: genai_types.Part) -> Optional[Any]:
    """Convert a Google GenAI Part to an A2A Part."""
    kind = _get_genai_part_kind(part)
    converter = _GENAI_KIND_CONVERTERS.get(kind) if kind else None
    if converter:
        return converter(part)
    logger.warning("Cannot convert unsupported GenAI part kind: %s, part: %s", kind, part)
    return None


def _normalize_function_call_data(data: Any) -> dict[str, Any]:
    """Parse args JSON string to dict and drop extra 'type' for FunctionCall."""
    if not isinstance(data, dict):
        return dict(data) if hasattr(data, "items") else {}
    out = dict(data)
    if A2A_DATA_FIELD_TOOL_CALL_ARGS in out and isinstance(out[A2A_DATA_FIELD_TOOL_CALL_ARGS], str):
        try:
            out[A2A_DATA_FIELD_TOOL_CALL_ARGS] = json.loads(out[A2A_DATA_FIELD_TOOL_CALL_ARGS])
        except (json.JSONDecodeError, TypeError):
            out[A2A_DATA_FIELD_TOOL_CALL_ARGS] = {}
    out.pop("type", None)
    return out


def _normalize_function_response_data(data: Any) -> dict[str, Any]:
    """Parse response for FunctionResponse; JSON string to dict, or wrap plain string in {\"content\": ...}."""
    if not isinstance(data, dict):
        return dict(data) if hasattr(data, "items") else {}
    out = dict(data)
    if A2A_DATA_FIELD_TOOL_CALL_RESPONSE in out and isinstance(out[A2A_DATA_FIELD_TOOL_CALL_RESPONSE], str):
        raw = out[A2A_DATA_FIELD_TOOL_CALL_RESPONSE]
        try:
            out[A2A_DATA_FIELD_TOOL_CALL_RESPONSE] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            out[A2A_DATA_FIELD_TOOL_CALL_RESPONSE] = {"content": raw}
    return out


def _convert_streaming_function_call_delta(data: Any) -> genai_types.Part:
    d = data or {}
    return genai_types.Part(function_call=genai_types.FunctionCall(
        id=d.get("id"),
        name=d.get("name"),
        args={TOOL_STREAMING_ARGS: d.get("delta_args", "")},
    ), )


def _a2a_data_to_dict(data: Any) -> dict[str, Any]:
    """Convert a protobuf ``Value`` data field back to a plain dict."""
    if isinstance(data, struct_pb2.Value):
        return MessageToDict(data)
    if isinstance(data, dict):
        return data
    return {}


_A2A_DATA_TYPE_CONVERTERS: dict[str, callable] = {
    A2A_DATA_PART_METADATA_TYPE_FUNCTION_CALL:
    lambda d: genai_types.Part(function_call=genai_types.FunctionCall.model_validate(_normalize_function_call_data(d),
                                                                                     by_alias=True), ),
    A2A_DATA_PART_METADATA_TYPE_FUNCTION_RESPONSE:
    lambda d: genai_types.Part(function_response=genai_types.FunctionResponse.model_validate(
        _normalize_function_response_data(d), by_alias=True), ),
    A2A_DATA_PART_METADATA_TYPE_CODE_EXECUTION_RESULT:
    lambda d: genai_types.Part(code_execution_result=genai_types.CodeExecutionResult.model_validate(d, by_alias=True),
                               ),
    A2A_DATA_PART_METADATA_TYPE_EXECUTABLE_CODE:
    lambda d: genai_types.Part(executable_code=genai_types.ExecutableCode.model_validate(d, by_alias=True), ),
    A2A_DATA_PART_METADATA_TYPE_STREAMING_FUNCTION_CALL_DELTA:
    _convert_streaming_function_call_delta,
}


def _convert_a2a_data_part(part: Any) -> Optional[genai_types.Part]:
    """Convert an A2A data Part to a GenAI Part based on metadata type.

    In 1.x the data is a ``google.protobuf.Value``; it is read back via
    ``MessageToDict`` so the converter lambdas receive plain dicts.
    """
    metadata = getattr(part, "metadata", None)
    metadata_type = get_metadata(metadata, A2A_DATA_PART_METADATA_TYPE_KEY)
    data = _a2a_data_to_dict(getattr(part, "data", None))
    converter = _A2A_DATA_TYPE_CONVERTERS.get(metadata_type)
    if converter:
        return converter(data)
    return genai_types.Part(text=json.dumps(data))


def convert_a2a_part_to_genai_part(a2a_part: Any) -> Optional[genai_types.Part]:
    """Convert an A2A Part to a Google GenAI Part."""
    if has_field(a2a_part, "text"):
        thought = _to_bool_metadata(get_metadata(getattr(a2a_part, "metadata", None), "thought"))
        kwargs: dict[str, Any] = {"text": a2a_part.text}
        if thought is not None:
            kwargs["thought"] = thought
        return genai_types.Part(**kwargs)

    if has_field(a2a_part, "url"):
        return genai_types.Part(file_data=genai_types.FileData(file_uri=a2a_part.url, mime_type=a2a_part.media_type), )

    if has_field(a2a_part, "raw"):
        return genai_types.Part(inline_data=genai_types.Blob(
            data=a2a_part.raw,
            mime_type=a2a_part.media_type,
        ))

    if has_field(a2a_part, "data"):
        return _convert_a2a_data_part(a2a_part)

    logger.warning("Cannot convert unsupported part type for A2A part: %s", a2a_part)
    return None
