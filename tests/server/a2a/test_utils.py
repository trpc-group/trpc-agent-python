# -*- coding: utf-8 -*-
"""Unit tests for trpc_agent_sdk.server.a2a._utils."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.server.a2a._utils import get_metadata, metadata_is_true, set_metadata


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
