# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Extended tests for evaluation criteria (_eval_criterion): regex, compare, JSON deep equal, tool trajectory."""

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import FinalResponseCriterion
from trpc_agent_sdk.evaluation import JSONCriterion
from trpc_agent_sdk.evaluation import TextCriterion
from trpc_agent_sdk.evaluation import ToolTrajectoryCriterion


class TestTextCriterionRegex:
    """Test suite for TextCriterion regex matching."""

    def test_regex_match(self):
        """Test regex pattern matching."""
        c = TextCriterion(match="regex")
        assert c.matches("hello world 123", r"\d+") is True

    def test_regex_no_match(self):
        """Test regex pattern not matching."""
        c = TextCriterion(match="regex")
        assert c.matches("hello world", r"\d+") is False

    def test_regex_invalid_pattern(self):
        """Test invalid regex pattern returns False."""
        c = TextCriterion(match="regex")
        assert c.matches("hello", r"[invalid") is False

    def test_regex_case_insensitive(self):
        """Test regex with case_insensitive."""
        c = TextCriterion(match="regex", case_insensitive=True)
        assert c.matches("Hello World", r"hello") is True


class TestTextCriterionCompare:
    """Test suite for TextCriterion custom compare."""

    def test_custom_compare_overrides(self):
        """Test custom compare overrides match strategy."""
        c = TextCriterion(compare=lambda a, e: len(a) > len(e))
        assert c.matches("longer", "short") is True
        assert c.matches("hi", "hello") is False

    def test_match_validator_normalizes(self):
        """Test match validator normalizes whitespace and case."""
        c = TextCriterion(match="  EXACT  ")
        assert c.match == "exact"

    def test_from_dict_empty_returns_none(self):
        """Test from_dict with empty dict returns None."""
        assert TextCriterion.from_dict({}) is None


class TestJSONCriterionDeepEqual:
    """Test suite for JSONCriterion deep equality."""

    def test_none_both(self):
        """Test both None matches."""
        c = JSONCriterion()
        assert c.matches(None, None) is True

    def test_type_mismatch(self):
        """Test type mismatch returns False."""
        c = JSONCriterion()
        assert c.matches({"a": 1}, [1]) is False

    def test_number_tolerance(self):
        """Test number_tolerance for float comparison."""
        c = JSONCriterion(number_tolerance=0.1)
        assert c.matches({"val": 1.05}, {"val": 1.0}) is True
        assert c.matches({"val": 1.5}, {"val": 1.0}) is False

    def test_default_tolerance(self):
        """Test default tolerance (1e-6) works."""
        c = JSONCriterion()
        assert c.matches(1.0000001, 1.0) is True
        assert c.matches(1.001, 1.0) is False

    def test_list_comparison(self):
        """Test list comparison."""
        c = JSONCriterion()
        assert c.matches([1, 2, 3], [1, 2, 3]) is True
        assert c.matches([1, 2], [1, 2, 3]) is False

    def test_nested_dict(self):
        """Test nested dict comparison."""
        c = JSONCriterion()
        assert c.matches({"a": {"b": 1}}, {"a": {"b": 1}}) is True
        assert c.matches({"a": {"b": 1}}, {"a": {"b": 2}}) is False

    def test_dict_keys_mismatch(self):
        """Test dict with different keys returns False."""
        c = JSONCriterion()
        assert c.matches({"a": 1}, {"b": 1}) is False

    def test_ignore_tree_nested(self):
        """Test ignore_tree with nested removal."""
        c = JSONCriterion(ignore_tree={"meta": {"ts": True}})
        a = {"val": 1, "meta": {"ts": 123, "src": "test"}}
        e = {"val": 1, "meta": {"src": "test"}}
        assert c.matches(a, e) is True

    def test_ignore_tree_non_dict_passthrough(self):
        """Test ignore_tree on non-dict value passes through."""
        c = JSONCriterion(ignore_tree={"id": True})
        assert c.matches("hello", "hello") is True

    def test_custom_compare(self):
        """Test custom compare overrides built-in."""
        c = JSONCriterion(compare=lambda a, e: True)
        assert c.matches({"a": 1}, {"b": 2}) is True

    def test_ignore_always_true(self):
        """Test ignore flag always returns True."""
        c = JSONCriterion(ignore=True)
        assert c.matches({"a": 1}, {"b": 2}) is True

    def test_dict_matches_dict(self):
        """Test plain dict matches dict."""
        c = JSONCriterion()
        assert c.matches({"a": 1}, {"a": 1}) is True

    def test_from_dict_with_alias(self):
        """Test from_dict with alias keys."""
        c = JSONCriterion.from_dict({"ignoreTree": {"id": True}, "numberTolerance": 0.01})
        assert c is not None
        assert c.ignore_tree == {"id": True}
        assert c.number_tolerance == 0.01


class TestToolTrajectoryCriterionExtended:
    """Extended tests for ToolTrajectoryCriterion."""

    def _fc(self, name, args=None):
        return type("FC", (), {"name": name, "args": args or {}})()

    def test_compare_override(self):
        """Test custom compare overrides matching."""
        c = ToolTrajectoryCriterion(compare=lambda a, e: True)
        assert c.matches([], [self._fc("x")]) is True

    def test_subset_matching_empty_expected(self):
        """Test subset_matching with empty expected always True."""
        c = ToolTrajectoryCriterion(subset_matching=True)
        assert c.matches([self._fc("a")], []) is True

    def test_subset_matching_not_enough_actual(self):
        """Test subset_matching fails when actual < expected."""
        c = ToolTrajectoryCriterion(subset_matching=True)
        assert c.matches([], [self._fc("a")]) is False

    def test_non_order_sensitive_greedy(self):
        """Test non-order-sensitive greedy matching."""
        c = ToolTrajectoryCriterion(order_sensitive=False)
        assert c.matches([self._fc("b"), self._fc("a")], [self._fc("a"), self._fc("b")]) is True

    def test_non_order_sensitive_no_match(self):
        """Test non-order-sensitive fails when no match."""
        c = ToolTrajectoryCriterion(order_sensitive=False)
        assert c.matches([self._fc("a")], [self._fc("b")]) is False

    def test_order_sensitive_subset_sliding(self):
        """Test order_sensitive + subset_matching sliding window."""
        c = ToolTrajectoryCriterion(order_sensitive=True, subset_matching=True)
        assert c.matches([self._fc("a"), self._fc("b"), self._fc("c")], [self._fc("a"), self._fc("c")]) is True

    def test_order_sensitive_subset_not_found(self):
        """Test order_sensitive + subset_matching fails when expected not in order."""
        c = ToolTrajectoryCriterion(order_sensitive=True, subset_matching=True)
        assert c.matches([self._fc("b"), self._fc("a")], [self._fc("a"), self._fc("b")]) is False

    def test_length_mismatch_not_subset(self):
        """Test non-subset length mismatch returns False."""
        c = ToolTrajectoryCriterion()
        assert c.matches([self._fc("a"), self._fc("b")], [self._fc("a")]) is False

    def test_from_dict_builds(self):
        """Test from_dict builds criterion."""
        c = ToolTrajectoryCriterion.from_dict({"order_sensitive": True, "subset_matching": False})
        assert c.order_sensitive is True
        assert c.subset_matching is False

    def test_pair_matches_args_mismatch(self):
        """Test _pair_matches fails on args mismatch."""
        c = ToolTrajectoryCriterion()
        a = type("FC", (), {"name": "tool", "args": {"x": 1}})()
        e = type("FC", (), {"name": "tool", "args": {"x": 2}})()
        assert c.matches([a], [e]) is False


class TestFinalResponseCriterionExtended:
    """Extended tests for FinalResponseCriterion."""

    def test_json_config_only(self):
        """Test json_config only comparison."""
        c = FinalResponseCriterion(json_config={"match": "exact"})
        assert c.matches('{"a": 1}', '{"a": 1}') is True
        assert c.matches('{"a": 1}', '{"a": 2}') is False

    def test_both_text_and_json(self):
        """Test both text and json_config must match."""
        c = FinalResponseCriterion(text={"match": "exact"}, json_config={"match": "exact"})
        assert c.matches('{"a": 1}', '{"a": 1}') is True
        assert c.matches("hello", '{"a": 1}') is False

    def test_compare_override(self):
        """Test compare overrides text/json."""
        c = FinalResponseCriterion(compare=lambda a, e: a == e)
        assert c.matches("hello", "hello") is True

    def test_text_to_json_invalid(self):
        """Test _text_to_json with invalid JSON returns None."""
        c = FinalResponseCriterion(json_config={"match": "exact"})
        assert c.matches("not json", "not json") is True

    def test_text_to_json_dict_passthrough(self):
        """Test _text_to_json passes through dict."""
        c = FinalResponseCriterion(json_config={"match": "exact"})
        assert c.matches({"a": 1}, {"a": 1}) is True

    def test_content_to_text_none(self):
        """Test _content_to_text with None returns empty."""
        c = FinalResponseCriterion(text={"match": "exact"})
        assert c.matches(None, "") is True

    def test_content_to_text_str(self):
        """Test _content_to_text with str returns as-is."""
        c = FinalResponseCriterion(text={"match": "exact"})
        assert c.matches("hello", "hello") is True

    def test_content_to_text_fallback_str(self):
        """Test _content_to_text falls back to str()."""
        c = FinalResponseCriterion(text={"match": "contains"})
        assert c.matches(42, "42") is True

    def test_json_config_empty_string(self):
        """Test _text_to_json with empty string returns None."""
        c = FinalResponseCriterion(json_config={"match": "exact"})
        assert c.matches("", "") is True

    def test_from_dict_with_alias(self):
        """Test from_dict with alias keys."""
        c = FinalResponseCriterion.from_dict({"textStrategy": {"match": "contains"}})
        assert c is not None
        assert c.matches("hello world", "world") is True
