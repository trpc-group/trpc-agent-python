# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Evaluation criterion definitions.

Defines TextCriterion, JSONCriterion, ToolTrajectoryCriterion, and
FinalResponseCriterion for string, JSON, tool trajectory, and final response
comparison in evaluation.
"""

from __future__ import annotations

import copy
from typing import Any
from typing import Callable
from typing import Literal
from typing import Optional

from pydantic import AliasChoices
from pydantic import Field
from pydantic import field_validator

from ._common import EvalBaseModel


class TextCriterion(EvalBaseModel):
    """Criterion for comparing two strings (e.g. tool name, final response text).

    Evaluation order: ignore (skip) -> compare (custom) -> match strategy.
    """

    match: Literal["exact", "contains", "regex"] = Field(
        default="exact",
        description="Match strategy: exact, contains, or regex.",
        validation_alias=AliasChoices("match", "match_strategy"),
    )
    case_insensitive: bool = Field(
        default=False,
        description="If True, ignore case when comparing.",
    )
    ignore: bool = Field(
        default=False,
        description="If True, skip comparison and always treat as match.",
    )
    compare: Optional[Callable[[str, str], bool]] = Field(
        default=None,
        description=("Custom compare (actual, expected) -> bool. When set, overrides "
                     "match strategy. Not loadable from JSON; set in code only."),
        exclude=True,
    )

    @field_validator("match", mode="before")
    @classmethod
    def _normalize_match(cls, v: object) -> object:
        """Normalize match string to lowercase and strip whitespace."""
        if isinstance(v, str):
            return v.strip().lower()
        return v

    def matches(self, actual: str, expected: str) -> bool:
        """Return True if actual matches expected under this criterion.

        Args:
            actual: The actual string (e.g. from agent output).
            expected: The expected string (e.g. from eval set).

        Returns:
            True if match, False otherwise. None is treated as empty string.
        """
        if self.ignore:
            return True
        a = actual if actual is not None else ""
        e = expected if expected is not None else ""
        if self.compare is not None:
            return self.compare(a, e)
        if self.case_insensitive:
            a, e = a.lower(), e.lower()
        if self.match == "exact":
            return a == e
        if self.match == "contains":
            return e in a
        if self.match == "regex":
            import re
            try:
                return bool(re.search(e, a))
            except re.error:
                return False
        return a == e

    @classmethod
    def from_dict(cls, d: dict | None) -> TextCriterion | None:
        """Build TextCriterion from a config dict (e.g. from JSON).

        Args:
            d: Config dict. Keys: match or match_strategy, case_insensitive or
               caseInsensitive, ignore. compare cannot be set from dict.

        Returns:
            TextCriterion instance, or None if d is None.
        """
        if not d:
            return None
        return cls.model_validate(d)


class JSONCriterion(EvalBaseModel):
    """Criterion for comparing two JSON-like values (e.g. tool arguments, result).

    Evaluation order: ignore (skip) -> compare (custom) -> exact with
    ignore_tree and number_tolerance.
    """

    match: Literal["exact"] = Field(
        default="exact",
        description="Match strategy; only exact is supported.",
        validation_alias=AliasChoices("match", "match_strategy"),
    )
    ignore_tree: Optional[dict[str, Any]] = Field(
        default=None,
        description=("Keys to remove before compare. Leaf value True means drop that key "
                     "(e.g. {\"id\": true, \"meta\": {\"ts\": true}})."),
        validation_alias=AliasChoices("ignore_tree", "ignoreTree"),
    )
    number_tolerance: Optional[float] = Field(
        default=None,
        description="Numeric comparison tolerance; default 1e-6 when None.",
        validation_alias=AliasChoices("number_tolerance", "numberTolerance"),
    )
    ignore: bool = Field(
        default=False,
        description="If True, skip comparison and always treat as match.",
    )
    compare: Optional[Callable[[Any, Any], bool]] = Field(
        default=None,
        description=("Custom compare (actual, expected) -> bool. When set, overrides "
                     "built-in logic. Not loadable from JSON; set in code only."),
        exclude=True,
    )

    @field_validator("match", mode="before")
    @classmethod
    def _normalize_match(cls, v: object) -> object:
        """Normalize match string to lowercase and strip whitespace."""
        if isinstance(v, str):
            return v.strip().lower()
        return v

    def matches(self, actual: Any, expected: Any) -> bool:
        """Return True if actual matches expected under this criterion.

        Args:
            actual: The actual value (e.g. tool args or result).
            expected: The expected value (e.g. from eval set).

        Returns:
            True if match, False otherwise.
        """
        if self.ignore:
            return True
        if self.compare is not None:
            return self.compare(actual, expected)
        a = self._normalize_to_dict(actual)
        e = self._normalize_to_dict(expected)
        if self.ignore_tree:
            a = self._apply_ignore_tree(a, self.ignore_tree)
            e = self._apply_ignore_tree(e, self.ignore_tree)
        return self._json_deep_equal(a, e)

    def _normalize_to_dict(self, val: Any) -> Any:
        """Normalize value to dict for comparison; leave other types as-is."""
        if val is None:
            return None
        if isinstance(val, dict):
            return copy.deepcopy(val)
        if hasattr(val, "items"):
            return dict(val)
        return val

    def _apply_ignore_tree(self, obj: Any, tree: dict[str, Any]) -> Any:
        """Return a copy of obj with keys in tree removed (recursive)."""
        if not isinstance(obj, dict):
            return obj
        out = copy.deepcopy(obj)
        for key, sub in tree.items():
            if key not in out:
                continue
            if sub is True:
                del out[key]
            elif isinstance(sub, dict) and isinstance(out.get(key), dict):
                out[key] = self._apply_ignore_tree(out[key], sub)
        return out

    def _json_deep_equal(self, actual: Any, expected: Any) -> bool:
        """Recursive equality using self.number_tolerance for numeric comparison."""
        if actual is None and expected is None:
            return True
        if type(actual) is not type(expected):
            return False
        tol = 1e-6 if self.number_tolerance is None else self.number_tolerance
        if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
            return abs(float(actual) - float(expected)) <= tol
        if isinstance(actual, dict) and isinstance(expected, dict):
            if set(actual.keys()) != set(expected.keys()):
                return False
            return all(self._json_deep_equal(actual[k], expected[k]) for k in actual)
        if isinstance(actual, list) and isinstance(expected, list):
            if len(actual) != len(expected):
                return False
            return all(self._json_deep_equal(actual[i], expected[i]) for i in range(len(actual)))
        return actual == expected

    @classmethod
    def from_dict(cls, d: dict | None) -> JSONCriterion | None:
        """Build JSONCriterion from a config dict (e.g. from JSON).

        Args:
            d: Config dict. Keys: match or match_strategy, ignore_tree or
               ignoreTree, number_tolerance or numberTolerance, ignore.
               compare cannot be set from dict.

        Returns:
            JSONCriterion instance, or None if d is None.
        """
        if not d:
            return None
        return cls.model_validate(d)


class ToolTrajectoryCriterion(EvalBaseModel):
    """Criterion for comparing tool call trajectories (name, arguments, result).

    Each tool uses a strategy: name (TextCriterion), arguments (JSONCriterion),
    result (JSONCriterion). default applies to all tools; overrides per tool name.
    """

    default: Optional[dict[str, Any]] = Field(
        default=None,
        description=("Default strategy for all tools: name, arguments, result "
                     "(each a dict for TextCriterion or JSONCriterion)."),
        validation_alias=AliasChoices("default", "default_strategy", "defaultStrategy"),
    )
    overrides: Optional[dict[str, dict[str, Any]]] = Field(
        default=None,
        description=("Per-tool overrides: tool_name -> { name?, arguments?, result? }. "
                     "Overrides take precedence over default."),
        validation_alias=AliasChoices("overrides", "tool_strategy", "toolStrategy"),
    )
    order_sensitive: bool = Field(
        default=False,
        description=("If True, actual tool calls must match expected in order. "
                     "If False, matching may be out-of-order (e.g. bipartite matching)."),
        validation_alias=AliasChoices("order_sensitive", "orderSensitive"),
    )
    subset_matching: bool = Field(
        default=False,
        description=("If True, expected is a subset: actual may have extra tool calls; "
                     "all expected tools must still match. If False, counts must match."),
        validation_alias=AliasChoices("subset_matching", "subsetMatching"),
    )
    compare: Optional[Callable[[Any, Any], bool]] = Field(
        default=None,
        description=("Custom compare (actual_tool_calls, expected_tool_calls) -> bool. "
                     "When set, overrides built-in per-tool comparison. Not loadable from JSON."),
        exclude=True,
    )

    def get_strategy_for_tool(self, tool_name: str) -> dict[str, Any]:
        """Return merged strategy for the tool (overrides override default).

        Args:
            tool_name: Name of the tool (e.g. get_weather).

        Returns:
            Dict with optional keys name, arguments, result (each a criterion
            config dict). Missing keys mean no criterion (evaluator uses default).
        """
        base = self.default or {}
        override = (self.overrides or {}).get(tool_name) or {}
        merged = dict(base)
        for key, value in override.items():
            if value is not None:
                merged[key] = value
        return merged

    def _pair_matches(self, actual_one: Any, expected_one: Any) -> bool:
        """Return True if one actual tool call matches one expected under strategy."""
        name_a = getattr(actual_one, "name", None) or ""
        name_e = getattr(expected_one, "name", None) or ""
        strategy = self.get_strategy_for_tool(name_a or name_e or "")
        name_c = TextCriterion.from_dict(strategy.get("name")) or TextCriterion()
        args_c = JSONCriterion.from_dict(strategy.get("arguments")) or JSONCriterion()
        if not name_c.matches(name_a, name_e):
            return False
        args_a = getattr(actual_one, "args", None)
        args_e = getattr(expected_one, "args", None)
        if not args_c.matches(args_a, args_e):
            return False
        return True

    def matches(
        self,
        actual_tool_calls: list[Any],
        expected_tool_calls: list[Any],
    ) -> bool:
        """Return True if actual tool call list matches expected under this criterion.

        Uses compare if set; else order_sensitive / subset_matching and
        per-tool strategy (name + arguments) for each pair.

        Args:
            actual_tool_calls: List of tool calls (each has .name, .args).
            expected_tool_calls: List of expected tool calls.

        Returns:
            True if match, False otherwise.
        """
        if self.compare is not None:
            return self.compare(actual_tool_calls, expected_tool_calls)
        if not expected_tool_calls:
            return not actual_tool_calls if not self.subset_matching else True
        if not self.subset_matching and len(actual_tool_calls) != len(expected_tool_calls):
            return False
        if self.subset_matching and len(actual_tool_calls) < len(expected_tool_calls):
            return False

        if self.order_sensitive:
            if self.subset_matching:
                j = 0
                for exp in expected_tool_calls:
                    while j < len(actual_tool_calls):
                        if self._pair_matches(actual_tool_calls[j], exp):
                            j += 1
                            break
                        j += 1
                    else:
                        return False
                return True
            if len(actual_tool_calls) != len(expected_tool_calls):
                return False
            return all(self._pair_matches(a, e) for a, e in zip(actual_tool_calls, expected_tool_calls))

        # Not order_sensitive: find a 1-1 matching (greedy).
        used = [False] * len(actual_tool_calls)
        for exp in expected_tool_calls:
            found = False
            for i, act in enumerate(actual_tool_calls):
                if used[i]:
                    continue
                if self._pair_matches(act, exp):
                    used[i] = True
                    found = True
                    break
            if not found:
                return False
        return True

    @classmethod
    def from_dict(cls, d: dict | None) -> ToolTrajectoryCriterion | None:
        """Build ToolTrajectoryCriterion from a config dict (e.g. from JSON).

        Args:
            d: Config dict with default or default_strategy, overrides or
               tool_strategy.

        Returns:
            ToolTrajectoryCriterion instance, or None if d is None.
        """
        if not d:
            return None
        return cls.model_validate(d)


class FinalResponseCriterion(EvalBaseModel):
    """Criterion for comparing agent final responses (e.g. Content or text).

    Supports text comparison and/or JSON comparison; when both are set,
    both must pass (AND). Compare overrides built-in logic when set.
    """

    text: Optional[dict[str, Any]] = Field(
        default=None,
        description=("Text comparison strategy: dict for TextCriterion (match, "
                     "case_insensitive, ignore)."),
        validation_alias=AliasChoices("text", "text_strategy", "textStrategy"),
    )
    json_config: Optional[dict[str, Any]] = Field(
        default=None,
        description=("JSON comparison strategy: dict for JSONCriterion (ignore_tree, "
                     "number_tolerance, ignore). Content is parsed as JSON then compared. "
                     "When both text and json_config are set, both must match."),
        validation_alias=AliasChoices("json", "json_strategy", "jsonStrategy"),
    )
    compare: Optional[Callable[[Any, Any], bool]] = Field(
        default=None,
        description=("Custom compare (actual, expected) -> bool. When set, overrides "
                     "text and json. Not loadable from JSON; set in code only."),
        exclude=True,
    )

    def _content_to_text(self, value: Any) -> str:
        """Normalize value to string; Content-like uses parts[].text."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        parts = getattr(value, "parts", None)
        if parts is not None:
            return "\n".join(getattr(p, "text", "") or "" for p in parts)
        return str(value)

    def _text_to_json(self, raw: Any) -> Any:
        """Parse value to JSON for json strategy; on failure return None."""
        s = self._content_to_text(raw) if not isinstance(raw, (dict, list)) else raw
        if isinstance(s, (dict, list)):
            return s
        s = (s or "").strip()
        if not s:
            return None
        import json
        try:
            return json.loads(s)
        except (json.JSONDecodeError, TypeError):
            return None

    def matches(self, actual: Any, expected: Any) -> bool:
        """Return True if actual final response matches expected.

        Compare overrides when set. When both text and json_config are set,
        both checks must pass. Returns False when neither strategy is set.
        """
        if self.compare is not None:
            return self.compare(actual, expected)
        if self.text is None and self.json_config is None:
            return False
        text_ok = True
        json_ok = True
        if self.text is not None:
            a = self._content_to_text(actual)
            e = self._content_to_text(expected)
            text_c = TextCriterion.from_dict(self.text)
            text_ok = text_c.matches(a, e)
        if self.json_config is not None:
            a_j = self._text_to_json(actual)
            e_j = self._text_to_json(expected)
            json_c = JSONCriterion.from_dict(self.json_config)
            json_ok = json_c.matches(a_j, e_j)
        if self.text is not None and self.json_config is not None:
            return text_ok and json_ok
        if self.json_config is not None:
            return json_ok
        return text_ok

    @classmethod
    def from_dict(cls, d: dict | None) -> FinalResponseCriterion | None:
        """Build FinalResponseCriterion from a config dict (e.g. from JSON).

        Args:
            d: Config dict. Keys: text or text_strategy or textStrategy (dict for
               TextCriterion), json or json_strategy or jsonStrategy (dict for
               JSONCriterion). compare cannot be set from dict.

        Returns:
            FinalResponseCriterion instance, or None if d is None.
        """
        if d is None:
            return None
        return cls.model_validate(d)
