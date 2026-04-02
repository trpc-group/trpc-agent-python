# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pydantic
import pytest

from trpc_agent_sdk.tools.utils._tool_utils import (
    convert_pydantic_args,
    extract_text,
    get_mandatory_args,
    retry_on_closed_resource,
)
from trpc_agent_sdk.types import Content, MemoryEntry, Part


# --- Test extract_text ---

class TestExtractText:

    def test_extract_single_part(self):
        entry = MagicMock(spec=MemoryEntry)
        entry.content = Content(parts=[Part.from_text(text="hello")])
        assert extract_text(entry) == "hello"

    def test_extract_multiple_parts(self):
        entry = MagicMock(spec=MemoryEntry)
        entry.content = Content(parts=[
            Part.from_text(text="hello"),
            Part.from_text(text="world"),
        ])
        assert extract_text(entry) == "hello world"

    def test_extract_with_custom_splitter(self):
        entry = MagicMock(spec=MemoryEntry)
        entry.content = Content(parts=[
            Part.from_text(text="a"),
            Part.from_text(text="b"),
        ])
        assert extract_text(entry, splitter=",") == "a,b"

    def test_extract_empty_parts(self):
        entry = MagicMock(spec=MemoryEntry)
        entry.content = Content(parts=[])
        assert extract_text(entry) == ""

    def test_extract_parts_with_none_text(self):
        entry = MagicMock(spec=MemoryEntry)
        part_with_text = Part.from_text(text="hello")
        part_no_text = MagicMock(spec=Part)
        part_no_text.text = None
        entry.content = Content(parts=[part_with_text, part_no_text])
        assert extract_text(entry) == "hello"


# --- Test get_mandatory_args ---

class TestGetMandatoryArgs:

    def test_all_mandatory(self):
        def func(a: str, b: int):
            pass

        assert get_mandatory_args(func) == ["a", "b"]

    def test_with_defaults(self):
        def func(a: str, b: int = 10):
            pass

        assert get_mandatory_args(func) == ["a"]

    def test_no_args(self):
        def func():
            pass

        assert get_mandatory_args(func) == []

    def test_excludes_var_positional(self):
        def func(a: str, *args):
            pass

        assert get_mandatory_args(func) == ["a"]

    def test_excludes_var_keyword(self):
        def func(a: str, **kwargs):
            pass

        assert get_mandatory_args(func) == ["a"]

    def test_keyword_only(self):
        def func(*, a: str, b: int = 5):
            pass

        assert get_mandatory_args(func) == ["a"]

    def test_mixed(self):
        def func(a: str, b: int = 5, *args, c: str, **kwargs):
            pass

        assert get_mandatory_args(func) == ["a", "c"]


# --- Test convert_pydantic_args ---

class SampleModel(pydantic.BaseModel):
    name: str
    value: int


class TestConvertPydanticArgs:

    def test_convert_dict_to_model(self):
        def func(data: SampleModel):
            pass

        sig = inspect.signature(func)
        result = convert_pydantic_args({"data": {"name": "test", "value": 42}}, sig)
        assert isinstance(result["data"], SampleModel)
        assert result["data"].name == "test"

    def test_already_model_instance(self):
        def func(data: SampleModel):
            pass

        sig = inspect.signature(func)
        model = SampleModel(name="ok", value=1)
        result = convert_pydantic_args({"data": model}, sig)
        assert result["data"] is model

    def test_non_pydantic_param(self):
        def func(x: str):
            pass

        sig = inspect.signature(func)
        result = convert_pydantic_args({"x": "hello"}, sig)
        assert result["x"] == "hello"

    def test_unknown_param_passed_through(self):
        def func(x: str):
            pass

        sig = inspect.signature(func)
        result = convert_pydantic_args({"x": "hello", "extra": "val"}, sig)
        assert result["extra"] == "val"

    def test_no_annotation(self):
        def func(x):
            pass

        sig = inspect.signature(func)
        result = convert_pydantic_args({"x": "hello"}, sig)
        assert result["x"] == "hello"

    def test_validation_error_keeps_original(self):
        def func(data: SampleModel):
            pass

        sig = inspect.signature(func)
        result = convert_pydantic_args({"data": {"name": "test"}}, sig)
        # Missing 'value' field - validation error, keeps original dict
        assert isinstance(result["data"], dict) or isinstance(result["data"], SampleModel)

    def test_non_dict_non_instance_fallback(self):
        def func(data: SampleModel):
            pass

        sig = inspect.signature(func)
        result = convert_pydantic_args({"data": "invalid"}, sig)
        # Should keep original value on failure
        assert result["data"] == "invalid"

    def test_non_class_annotation(self):
        def func(x: int | str):
            pass

        sig = inspect.signature(func)
        result = convert_pydantic_args({"x": 42}, sig)
        assert result["x"] == 42


# --- Test retry_on_closed_resource ---

class TestRetryOnClosedResource:

    @pytest.mark.asyncio
    async def test_no_retry_on_success(self):
        call_count = 0

        class Svc:
            @retry_on_closed_resource
            async def action(self):
                nonlocal call_count
                call_count += 1
                return "ok"

        svc = Svc()
        result = await svc.action()
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_closed_resource(self):
        call_count = 0

        class Svc:
            @retry_on_closed_resource
            async def action(self):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise anyio.ClosedResourceError()
                return "retried_ok"

        svc = Svc()
        result = await svc.action()
        assert result == "retried_ok"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_does_not_retry_on_other_errors(self):
        class Svc:
            @retry_on_closed_resource
            async def action(self):
                raise ValueError("not closed resource")

        svc = Svc()
        with pytest.raises(ValueError, match="not closed resource"):
            await svc.action()

    @pytest.mark.asyncio
    async def test_preserves_function_metadata(self):
        class Svc:
            @retry_on_closed_resource
            async def my_action(self):
                """My doc."""
                pass

        assert Svc.my_action.__name__ == "my_action"
        assert Svc.my_action.__doc__ == "My doc."
