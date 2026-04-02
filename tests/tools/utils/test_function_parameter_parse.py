# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from __future__ import annotations

import inspect
from typing import Any, Dict, List, Literal, Optional, Union

import pytest
from pydantic import BaseModel

from trpc_agent_sdk.tools._constants import DEFAULT_API_VARIANT
from trpc_agent_sdk.tools.utils._function_parameter_parse import (
    SCHEMA_FIELDS,
    _get_schema_fields,
    _is_builtin_primitive_or_compound,
    _is_default_value_compatible,
    _resolve_annotation,
    get_required_fields,
    parse_schema_from_parameter,
    register_checker,
)
from google.genai.types import Schema, Type


# --- Test _resolve_annotation ---

class TestResolveAnnotation:

    def test_non_string_passthrough(self):
        assert _resolve_annotation(str) is str
        assert _resolve_annotation(int) is int

    def test_string_builtin_type(self):
        assert _resolve_annotation("str") is str
        assert _resolve_annotation("int") is int
        assert _resolve_annotation("float") is float
        assert _resolve_annotation("bool") is bool

    def test_string_with_globals(self):
        class MyType:
            pass

        result = _resolve_annotation("MyType", {"MyType": MyType})
        assert result is MyType

    def test_unresolvable_string(self):
        result = _resolve_annotation("CompletelyUnknownType")
        assert result == "CompletelyUnknownType"

    def test_invocation_context_string(self):
        result = _resolve_annotation("InvocationContext")
        assert result is Any


# --- Test _is_builtin_primitive_or_compound ---

class TestIsBuiltinPrimitiveOrCompound:

    def test_builtin_types(self):
        assert _is_builtin_primitive_or_compound(str) is True
        assert _is_builtin_primitive_or_compound(int) is True
        assert _is_builtin_primitive_or_compound(float) is True
        assert _is_builtin_primitive_or_compound(bool) is True
        assert _is_builtin_primitive_or_compound(list) is True
        assert _is_builtin_primitive_or_compound(dict) is True
        assert _is_builtin_primitive_or_compound(Any) is True

    def test_non_builtin_types(self):
        assert _is_builtin_primitive_or_compound(BaseModel) is False
        assert _is_builtin_primitive_or_compound(type(None)) is False


# --- Test _is_default_value_compatible ---

class TestIsDefaultValueCompatible:

    def test_str_compatible(self):
        assert _is_default_value_compatible("hello", str) is True
        assert _is_default_value_compatible(42, str) is False

    def test_int_compatible(self):
        assert _is_default_value_compatible(42, int) is True
        assert _is_default_value_compatible("no", int) is False

    def test_float_compatible(self):
        assert _is_default_value_compatible(1.5, float) is True

    def test_bool_compatible(self):
        assert _is_default_value_compatible(True, bool) is True

    def test_dict_compatible(self):
        assert _is_default_value_compatible({}, dict) is True
        assert _is_default_value_compatible("no", dict) is False

    def test_union_type(self):
        assert _is_default_value_compatible("hello", Union[str, int]) is True
        assert _is_default_value_compatible(42, Union[str, int]) is True
        assert _is_default_value_compatible(1.5, Union[str, int]) is False

    def test_list_type(self):
        assert _is_default_value_compatible([1, 2], List[int]) is True
        assert _is_default_value_compatible("not_list", List[int]) is False

    def test_dict_generic(self):
        assert _is_default_value_compatible({}, Dict[str, int]) is True
        assert _is_default_value_compatible("no", Dict[str, int]) is False

    def test_literal_type(self):
        assert _is_default_value_compatible("a", Literal["a", "b"]) is True
        assert _is_default_value_compatible("c", Literal["a", "b"]) is False

    def test_complex_list_union(self):
        assert _is_default_value_compatible([1, "a", 1.1, True], List[Union[int, str, float, bool]]) is True

    def test_unrecognized_returns_false(self):
        class Custom:
            pass
        assert _is_default_value_compatible(Custom(), Custom) is False


# --- Test parse_schema_from_parameter ---

class SampleModel(BaseModel):
    name: str
    age: int


class TestParseSchemaFromParameter:

    def _make_param(self, name, annotation, default=inspect.Parameter.empty):
        return inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD, default=default, annotation=annotation)

    def test_str_param(self):
        param = self._make_param("x", str)
        schema = parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")
        assert schema.type == Type.STRING

    def test_int_param(self):
        param = self._make_param("x", int)
        schema = parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")
        assert schema.type == Type.INTEGER

    def test_float_param(self):
        param = self._make_param("x", float)
        schema = parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")
        assert schema.type == Type.NUMBER

    def test_bool_param(self):
        param = self._make_param("x", bool)
        schema = parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")
        assert schema.type == Type.BOOLEAN

    def test_list_param(self):
        param = self._make_param("x", list)
        schema = parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")
        assert schema.type == Type.ARRAY

    def test_dict_param(self):
        param = self._make_param("x", dict)
        schema = parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")
        assert schema.type == Type.OBJECT

    def test_str_with_default(self):
        param = self._make_param("x", str, default="hello")
        schema = parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")
        assert schema.type == Type.STRING
        assert schema.default == "hello"

    def test_incompatible_default_raises(self):
        param = self._make_param("x", str, default=42)
        with pytest.raises(ValueError, match="not compatible"):
            parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")

    def test_union_simple(self):
        param = self._make_param("x", Union[str, int])
        schema = parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")
        assert schema.any_of is not None
        assert len(schema.any_of) == 2

    def test_optional_type(self):
        param = self._make_param("x", Optional[str])
        schema = parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")
        assert schema.nullable is True

    def test_dict_generic(self):
        param = self._make_param("x", Dict[str, int])
        schema = parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")
        assert schema.type == Type.OBJECT

    def test_list_generic(self):
        param = self._make_param("x", List[str])
        schema = parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")
        assert schema.type == Type.ARRAY
        assert schema.items is not None
        assert schema.items.type == Type.STRING

    def test_literal(self):
        param = self._make_param("x", Literal["a", "b", "c"])
        schema = parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")
        assert schema.type == Type.STRING
        assert schema.enum == ["a", "b", "c"]

    def test_literal_non_string_raises(self):
        param = self._make_param("x", Literal[1, 2])
        with pytest.raises(ValueError, match="must be a list of strings"):
            parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")

    def test_pydantic_model(self):
        param = self._make_param("x", SampleModel)
        schema = parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")
        assert schema.type == Type.OBJECT
        assert "name" in schema.properties
        assert "age" in schema.properties

    def test_unsupported_type_raises(self):
        class CustomClass:
            pass
        param = self._make_param("x", CustomClass)
        with pytest.raises(ValueError, match="Failed to parse"):
            parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")

    def test_string_annotation_resolution(self):
        param = self._make_param("x", "str")
        schema = parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")
        assert schema.type == Type.STRING

    def test_optional_list(self):
        param = self._make_param("x", Optional[List[str]])
        schema = parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")
        assert schema.nullable is True

    def test_union_with_default(self):
        param = self._make_param("x", Union[str, int], default="hello")
        schema = parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")
        assert schema.default == "hello"

    def test_literal_with_default(self):
        param = self._make_param("x", Literal["a", "b"], default="a")
        schema = parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")
        assert schema.default == "a"

    def test_dict_generic_with_default(self):
        param = self._make_param("x", Dict[str, int], default={})
        schema = parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")
        assert schema.default == {}

    def test_list_generic_with_default(self):
        param = self._make_param("x", List[str], default=[])
        schema = parse_schema_from_parameter(DEFAULT_API_VARIANT, param, "test_func")
        assert schema.default == []


# --- Test get_required_fields ---

class TestGetRequiredFields:

    def test_basic_required_fields(self):
        schema = Schema(
            type=Type.OBJECT,
            properties={
                "name": Schema(type=Type.STRING),
                "age": Schema(type=Type.INTEGER),
            },
        )
        required = get_required_fields(schema)
        assert "name" in required
        assert "age" in required

    def test_nullable_field_not_required(self):
        schema = Schema(
            type=Type.OBJECT,
            properties={
                "name": Schema(type=Type.STRING),
                "optional_field": Schema(type=Type.STRING, nullable=True),
            },
        )
        required = get_required_fields(schema)
        assert "name" in required
        assert "optional_field" not in required

    def test_field_with_default_not_required(self):
        schema = Schema(
            type=Type.OBJECT,
            properties={
                "name": Schema(type=Type.STRING),
                "count": Schema(type=Type.INTEGER, default=0),
            },
        )
        required = get_required_fields(schema)
        assert "name" in required
        assert "count" not in required

    def test_no_properties_returns_none(self):
        schema = Schema(type=Type.OBJECT)
        assert get_required_fields(schema) is None


# --- Test register_checker ---

class TestRegisterChecker:

    def test_register_and_use_checker(self):
        test_variant = f"test_variant_{id(self)}"
        checker_called = False

        @register_checker(test_variant)
        def my_checker(schema: Schema) -> bool:
            nonlocal checker_called
            checker_called = True
            return True

        from trpc_agent_sdk.tools.utils._function_parameter_parse import _SchemaChecker
        checker = _SchemaChecker()
        result = checker.check(test_variant, Schema(type=Type.STRING))
        assert checker_called
        assert result is True


# --- Test SCHEMA_FIELDS ---

class TestSchemaFields:

    def test_schema_fields_is_set(self):
        assert isinstance(SCHEMA_FIELDS, set)

    def test_contains_common_fields(self):
        assert "type" in SCHEMA_FIELDS
        assert "properties" in SCHEMA_FIELDS

    def test_get_schema_fields_returns_set(self):
        result = _get_schema_fields()
        assert isinstance(result, set)
        assert len(result) > 0
