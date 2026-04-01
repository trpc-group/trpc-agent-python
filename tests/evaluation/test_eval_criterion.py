# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for evaluation criteria (_eval_criterion)."""

import pytest
from trpc_agent_sdk.evaluation import FinalResponseCriterion
from trpc_agent_sdk.evaluation import JSONCriterion
from trpc_agent_sdk.evaluation import TextCriterion
from trpc_agent_sdk.evaluation import ToolTrajectoryCriterion


class TestTextCriterion:
    """Test suite for TextCriterion."""

    def test_exact_match(self):
        """Test exact match."""
        c = TextCriterion(match="exact")
        assert c.matches("hello", "hello") is True
        assert c.matches("hello", "hi") is False

    def test_contains(self):
        """Test contains match."""
        c = TextCriterion(match="contains")
        assert c.matches("hello world", "world") is True
        assert c.matches("hello", "world") is False

    def test_case_insensitive(self):
        """Test case_insensitive."""
        c = TextCriterion(match="exact", case_insensitive=True)
        assert c.matches("Hello", "hello") is True

    def test_ignore_always_true(self):
        """Test ignore skips comparison."""
        c = TextCriterion(ignore=True)
        assert c.matches("a", "b") is True

    def test_none_treated_as_empty(self):
        """Test None treated as empty string."""
        c = TextCriterion(match="exact")
        assert c.matches(None, "") is True
        assert c.matches("", None) is True

    def test_from_dict_none(self):
        """Test from_dict(None) returns None."""
        assert TextCriterion.from_dict(None) is None

    def test_from_dict(self):
        """Test from_dict builds criterion."""
        c = TextCriterion.from_dict({"match": "contains", "case_insensitive": True})
        assert c is not None
        assert c.match == "contains"
        assert c.case_insensitive is True
        assert c.matches("HELLO", "hello") is True


class TestJSONCriterion:
    """Test suite for JSONCriterion."""

    def test_exact_match(self):
        """Test exact JSON match."""
        c = JSONCriterion()
        assert c.matches({"a": 1}, {"a": 1}) is True
        assert c.matches({"a": 1}, {"a": 2}) is False

    def test_ignore_tree(self):
        """Test ignore_tree drops keys before compare."""
        c = JSONCriterion(ignore_tree={"id": True})
        assert c.matches({"id": "x", "v": 1}, {"v": 1}) is True

    def test_from_dict_none(self):
        """Test from_dict(None) returns None."""
        assert JSONCriterion.from_dict(None) is None


class TestToolTrajectoryCriterion:
    """Test suite for ToolTrajectoryCriterion."""

    def test_get_strategy_for_tool(self):
        """Test get_strategy_for_tool merges default and overrides."""
        c = ToolTrajectoryCriterion(
            default={"name": {"match": "exact"}},
            overrides={"get_weather": {"arguments": {"ignore_tree": {"ts": True}}}},
        )
        s = c.get_strategy_for_tool("get_weather")
        assert s.get("name") == {"match": "exact"}
        assert s.get("arguments") == {"ignore_tree": {"ts": True}}
        s_default = c.get_strategy_for_tool("other_tool")
        assert s_default.get("name") == {"match": "exact"}

    def test_matches_empty_expected(self):
        """Test matches when expected is empty."""
        c = ToolTrajectoryCriterion()
        assert c.matches([], []) is True
        a = type("FC", (), {"name": "x", "args": {}})()
        assert c.matches([a], []) is False

    def test_matches_order_sensitive(self):
        """Test order_sensitive matching."""
        c = ToolTrajectoryCriterion(order_sensitive=True)
        a1 = type("FC", (), {"name": "a", "args": {}})()
        a2 = type("FC", (), {"name": "b", "args": {}})()
        e1 = type("FC", (), {"name": "a", "args": {}})()
        e2 = type("FC", (), {"name": "b", "args": {}})()
        assert c.matches([a1, a2], [e1, e2]) is True
        assert c.matches([a2, a1], [e1, e2]) is False

    def test_from_dict_none(self):
        """Test from_dict(None) returns None."""
        assert ToolTrajectoryCriterion.from_dict(None) is None


class TestFinalResponseCriterion:
    """Test suite for FinalResponseCriterion."""

    def test_no_text_or_json_returns_false(self):
        """Test matches returns False when neither text nor json_config set."""
        c = FinalResponseCriterion()
        assert c.matches("a", "a") is False

    def test_text_match(self):
        """Test text strategy."""
        c = FinalResponseCriterion(text={"match": "exact"})
        assert c.matches("hello", "hello") is True
        assert c.matches("hello", "hi") is False

    def test_content_like_to_text(self):
        """Test _content_to_text extracts text from Content-like."""
        c = FinalResponseCriterion(text={"match": "exact"})
        content_like = type("C", (), {"parts": [type("P", (), {"text": "hi"})()]})()
        assert c.matches(content_like, "hi") is True

    def test_from_dict_none(self):
        """Test from_dict(None) returns None."""
        assert FinalResponseCriterion.from_dict(None) is None

    def test_from_dict(self):
        """Test from_dict builds criterion."""
        c = FinalResponseCriterion.from_dict({"text": {"match": "contains"}})
        assert c is not None
        assert c.matches("hello world", "world") is True
