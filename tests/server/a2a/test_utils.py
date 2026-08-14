# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.a2a._utils."""

from __future__ import annotations

import pytest
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict

from trpc_agent_sdk.server.a2a._utils import get_metadata, has_field, metadata_is_true, set_metadata


class TestSetMetadata:
    def test_sets_value(self):
        d: dict = {}
        set_metadata(d, "key", "value")
        assert d["key"] == "value"

    def test_overwrites_existing(self):
        d = {"key": "old"}
        set_metadata(d, "key", "new")
        assert d["key"] == "new"

    def test_sets_none_value(self):
        d: dict = {}
        set_metadata(d, "key", None)
        assert d["key"] is None

    def test_sets_complex_value(self):
        d: dict = {}
        set_metadata(d, "nested", {"a": [1, 2, 3]})
        assert d["nested"] == {"a": [1, 2, 3]}


class TestGetMetadata:
    def test_returns_default_for_none_metadata(self):
        assert get_metadata(None, "key") is None

    def test_returns_custom_default_for_none_metadata(self):
        assert get_metadata(None, "key", "fallback") == "fallback"

    def test_returns_default_for_empty_dict(self):
        assert get_metadata({}, "key") is None

    def test_returns_value_when_key_exists(self):
        assert get_metadata({"key": "val"}, "key") == "val"

    def test_returns_default_when_key_missing(self):
        assert get_metadata({"other": 1}, "key", "default") == "default"

    def test_returns_falsy_value_when_present(self):
        assert get_metadata({"key": 0}, "key", 42) == 0
        assert get_metadata({"key": ""}, "key", "x") == ""
        assert get_metadata({"key": False}, "key", True) is False


class TestMetadataIsTrue:
    def test_true_bool(self):
        assert metadata_is_true({"k": True}, "k") is True

    def test_false_bool(self):
        assert metadata_is_true({"k": False}, "k") is False

    def test_string_true(self):
        assert metadata_is_true({"k": "true"}, "k") is True

    def test_string_true_case_insensitive(self):
        assert metadata_is_true({"k": "True"}, "k") is True
        assert metadata_is_true({"k": "TRUE"}, "k") is True

    def test_string_true_with_whitespace(self):
        assert metadata_is_true({"k": "  true  "}, "k") is True

    def test_string_false(self):
        assert metadata_is_true({"k": "false"}, "k") is False

    def test_string_non_boolean(self):
        assert metadata_is_true({"k": "yes"}, "k") is False

    def test_none_metadata(self):
        assert metadata_is_true(None, "k") is False

    def test_missing_key(self):
        assert metadata_is_true({"other": True}, "k") is False

    def test_integer_value(self):
        assert metadata_is_true({"k": 1}, "k") is False

    def test_none_value(self):
        assert metadata_is_true({"k": None}, "k") is False


class TestStructMetadata:
    def test_set_and_get_scalar_round_trip(self):
        s = struct_pb2.Struct()
        set_metadata(s, "key", "value")
        assert "key" in s
        assert s["key"] == "value"
        assert get_metadata(s, "key") == "value"

    def test_overwrite_existing(self):
        s = struct_pb2.Struct()
        set_metadata(s, "key", "old")
        set_metadata(s, "key", "new")
        assert s["key"] == "new"
        assert get_metadata(s, "key") == "new"

    def test_none_value_round_trip(self):
        s = struct_pb2.Struct()
        set_metadata(s, "key", None)
        assert "key" in s
        assert get_metadata(s, "key", "fallback") is None

    def test_nested_dict_round_trip(self):
        s = struct_pb2.Struct()
        set_metadata(s, "nested", {"a": [1, 2, 3]})
        assert "nested" in s
        # Protobuf Value stores numbers as double.
        assert MessageToDict(s)["nested"] == {"a": [1.0, 2.0, 3.0]}
        nested = get_metadata(s, "nested")
        assert list(nested["a"]) == [1.0, 2.0, 3.0]

    def test_empty_struct_returns_default(self):
        assert get_metadata(struct_pb2.Struct(), "key", "fallback") == "fallback"

    def test_missing_key_returns_default(self):
        s = struct_pb2.Struct()
        set_metadata(s, "other", 1)
        assert get_metadata(s, "key", "default") == "default"

    def test_numeric_round_trips_as_float(self):
        s = struct_pb2.Struct()
        set_metadata(s, "key", 0)
        assert get_metadata(s, "key", 42) == 0.0

    def test_falsy_string_and_bool_preserved(self):
        s = struct_pb2.Struct()
        set_metadata(s, "empty", "")
        set_metadata(s, "flag", False)
        assert get_metadata(s, "empty", "x") == ""
        assert get_metadata(s, "flag", True) is False

    def test_metadata_is_true_bool(self):
        s = struct_pb2.Struct()
        set_metadata(s, "k", True)
        assert metadata_is_true(s, "k") is True
        set_metadata(s, "k", False)
        assert metadata_is_true(s, "k") is False

    def test_metadata_is_true_string(self):
        s = struct_pb2.Struct()
        set_metadata(s, "k", "true")
        assert metadata_is_true(s, "k") is True
        set_metadata(s, "k", "  TRUE  ")
        assert metadata_is_true(s, "k") is True
        set_metadata(s, "k", "false")
        assert metadata_is_true(s, "k") is False

    def test_metadata_is_true_number_is_false(self):
        s = struct_pb2.Struct()
        set_metadata(s, "k", 1)
        assert metadata_is_true(s, "k") is False

    def test_metadata_is_true_missing_and_none(self):
        s = struct_pb2.Struct()
        assert metadata_is_true(s, "k") is False
        set_metadata(s, "k", None)
        assert metadata_is_true(s, "k") is False


class TestHasField:
    def test_protobuf_oneof_empty_is_unset(self):
        from a2a.types import Part as A2APart

        part = A2APart()
        assert has_field(part, "text") is False
        assert has_field(part, "data") is False

    def test_protobuf_oneof_text_is_set(self):
        from a2a.types import Part as A2APart

        part = A2APart(text="")
        assert has_field(part, "text") is True
        assert has_field(part, "data") is False

    def test_duck_typed_without_hasfield_uses_attribute(self):
        from types import SimpleNamespace

        part = SimpleNamespace(text="hello")
        assert has_field(part, "text") is True
        assert has_field(part, "data") is False

    def test_unknown_protobuf_field_is_unset(self):
        from a2a.types import Part as A2APart

        assert has_field(A2APart(text="hi"), "not_a_field") is False
