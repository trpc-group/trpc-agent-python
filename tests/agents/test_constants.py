# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for agent constants."""

from trpc_agent_sdk.agents._constants import (
    FILTER_NAME_SPLIT_NUM,
    MODEL_NAME,
    TOOL_CALL_INFO,
    TYPE_LABELS,
)


class TestFilterNameSplitNum:
    def test_value(self):
        assert FILTER_NAME_SPLIT_NUM == 2

    def test_type(self):
        assert isinstance(FILTER_NAME_SPLIT_NUM, int)


class TestModelName:
    def test_value(self):
        assert MODEL_NAME == "model_name"


class TestToolCallInfo:
    def test_value(self):
        assert TOOL_CALL_INFO == "tool_call_info"


class TestTypeLabels:
    def test_string_label(self):
        assert TYPE_LABELS["STRING"] == "string"

    def test_number_label(self):
        assert TYPE_LABELS["NUMBER"] == "number"

    def test_boolean_label(self):
        assert TYPE_LABELS["BOOLEAN"] == "boolean"

    def test_object_label(self):
        assert TYPE_LABELS["OBJECT"] == "object"

    def test_array_label(self):
        assert TYPE_LABELS["ARRAY"] == "array"

    def test_integer_label(self):
        assert TYPE_LABELS["INTEGER"] == "integer"

    def test_all_keys_present(self):
        expected = {"STRING", "NUMBER", "BOOLEAN", "OBJECT", "ARRAY", "INTEGER"}
        assert set(TYPE_LABELS.keys()) == expected

    def test_all_values_are_lowercase(self):
        for value in TYPE_LABELS.values():
            assert value == value.lower()
