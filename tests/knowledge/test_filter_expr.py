# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for KnowledgeFilterExpr and filter expression validation."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.knowledge._filter_expr import (
    KnowledgeFilterExpr,
    _ALL_OPERATORS,
    _COMPARISON_OPERATORS,
    _LOGICAL_OPERATORS,
)


# ---------------------------------------------------------------------------
# Module-level operator sets
# ---------------------------------------------------------------------------


class TestOperatorSets:
    def test_logical_operators(self):
        assert _LOGICAL_OPERATORS == {"and", "or"}

    def test_comparison_operators(self):
        expected = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "not in", "like", "not like", "between"}
        assert _COMPARISON_OPERATORS == expected

    def test_all_operators_is_union(self):
        assert _ALL_OPERATORS == _LOGICAL_OPERATORS | _COMPARISON_OPERATORS


# ---------------------------------------------------------------------------
# _normalize_operator (mode="before")
# ---------------------------------------------------------------------------


class TestNormalizeOperator:
    def test_non_dict_input_raises(self):
        with pytest.raises(ValueError, match="must be an object"):
            KnowledgeFilterExpr.model_validate("not a dict")

    def test_non_string_operator_raises(self):
        with pytest.raises(ValueError, match="operator must be a string"):
            KnowledgeFilterExpr.model_validate({"operator": 123, "field": "x", "value": 1})

    def test_missing_operator_raises(self):
        with pytest.raises(ValueError, match="operator must be a string"):
            KnowledgeFilterExpr.model_validate({"field": "x", "value": 1})

    def test_unsupported_operator_raises(self):
        with pytest.raises(ValueError, match="unsupported knowledge filter operator"):
            KnowledgeFilterExpr.model_validate({"operator": "xor", "field": "x", "value": 1})

    def test_operator_lowercased(self):
        expr = KnowledgeFilterExpr.model_validate({"operator": "EQ", "field": "name", "value": "alice"})
        assert expr.operator == "eq"

    def test_operator_stripped(self):
        expr = KnowledgeFilterExpr.model_validate({"operator": "  eq  ", "field": "name", "value": "alice"})
        assert expr.operator == "eq"

    def test_operator_strip_and_lower_combined(self):
        expr = KnowledgeFilterExpr.model_validate({"operator": " GT ", "field": "age", "value": 18})
        assert expr.operator == "gt"

    def test_existing_instance_passthrough(self):
        original = KnowledgeFilterExpr(operator="eq", field="name", value="bob")
        validated = KnowledgeFilterExpr.model_validate(original)
        assert validated.operator == "eq"
        assert validated.field == "name"
        assert validated.value == "bob"


# ---------------------------------------------------------------------------
# _validate_semantics – comparison operators
# ---------------------------------------------------------------------------


class TestComparisonOperators:
    @pytest.mark.parametrize("op", ["eq", "ne", "gt", "gte", "lt", "lte", "like", "not like"])
    def test_basic_comparison_valid(self, op):
        expr = KnowledgeFilterExpr(operator=op, field="status", value="active")
        assert expr.operator == op
        assert expr.field == "status"
        assert expr.value == "active"

    @pytest.mark.parametrize("op", ["eq", "ne", "gt", "gte", "lt", "lte", "like", "not like"])
    def test_basic_comparison_none_value_raises(self, op):
        with pytest.raises(ValueError, match="requires value"):
            KnowledgeFilterExpr(operator=op, field="status", value=None)

    @pytest.mark.parametrize("op", list(_COMPARISON_OPERATORS))
    def test_empty_field_raises(self, op):
        value = ["a"] if op in {"in", "not in"} else [1, 2] if op == "between" else "x"
        with pytest.raises(ValueError, match="requires non-empty field"):
            KnowledgeFilterExpr(operator=op, field="", value=value)

    @pytest.mark.parametrize("op", list(_COMPARISON_OPERATORS))
    def test_whitespace_only_field_raises(self, op):
        value = ["a"] if op in {"in", "not in"} else [1, 2] if op == "between" else "x"
        with pytest.raises(ValueError, match="requires non-empty field"):
            KnowledgeFilterExpr(operator=op, field="   ", value=value)

    def test_field_is_stripped(self):
        expr = KnowledgeFilterExpr(operator="eq", field="  name  ", value="alice")
        assert expr.field == "name"

    def test_numeric_value(self):
        expr = KnowledgeFilterExpr(operator="gt", field="age", value=18)
        assert expr.value == 18

    def test_float_value(self):
        expr = KnowledgeFilterExpr(operator="lte", field="score", value=0.95)
        assert expr.value == 0.95


# ---------------------------------------------------------------------------
# _validate_semantics – "in" / "not in"
# ---------------------------------------------------------------------------


class TestInOperator:
    @pytest.mark.parametrize("op", ["in", "not in"])
    def test_valid_list_value(self, op):
        expr = KnowledgeFilterExpr(operator=op, field="status", value=["active", "pending"])
        assert expr.value == ["active", "pending"]

    @pytest.mark.parametrize("op", ["in", "not in"])
    def test_empty_list_raises(self, op):
        with pytest.raises(ValueError, match="requires non-empty list"):
            KnowledgeFilterExpr(operator=op, field="status", value=[])

    @pytest.mark.parametrize("op", ["in", "not in"])
    def test_non_list_value_raises(self, op):
        with pytest.raises(ValueError, match="requires non-empty list"):
            KnowledgeFilterExpr(operator=op, field="status", value="active")

    @pytest.mark.parametrize("op", ["in", "not in"])
    def test_none_value_raises(self, op):
        with pytest.raises(ValueError, match="requires non-empty list"):
            KnowledgeFilterExpr(operator=op, field="status", value=None)

    def test_in_single_element(self):
        expr = KnowledgeFilterExpr(operator="in", field="id", value=[42])
        assert expr.value == [42]

    def test_not_in_mixed_types(self):
        expr = KnowledgeFilterExpr(operator="not in", field="tag", value=["a", 1, True])
        assert expr.value == ["a", 1, True]


# ---------------------------------------------------------------------------
# _validate_semantics – "between"
# ---------------------------------------------------------------------------


class TestBetweenOperator:
    def test_valid_between(self):
        expr = KnowledgeFilterExpr(operator="between", field="age", value=[18, 65])
        assert expr.value == [18, 65]

    def test_between_non_list_raises(self):
        with pytest.raises(ValueError, match="requires list value with exactly two items"):
            KnowledgeFilterExpr(operator="between", field="age", value=30)

    def test_between_empty_list_raises(self):
        with pytest.raises(ValueError, match="requires list value with exactly two items"):
            KnowledgeFilterExpr(operator="between", field="age", value=[])

    def test_between_single_item_raises(self):
        with pytest.raises(ValueError, match="requires list value with exactly two items"):
            KnowledgeFilterExpr(operator="between", field="age", value=[18])

    def test_between_three_items_raises(self):
        with pytest.raises(ValueError, match="requires list value with exactly two items"):
            KnowledgeFilterExpr(operator="between", field="age", value=[10, 20, 30])

    def test_between_with_floats(self):
        expr = KnowledgeFilterExpr(operator="between", field="score", value=[0.1, 0.9])
        assert expr.value == [0.1, 0.9]

    def test_between_with_strings(self):
        expr = KnowledgeFilterExpr(operator="between", field="date", value=["2024-01-01", "2024-12-31"])
        assert expr.value == ["2024-01-01", "2024-12-31"]


# ---------------------------------------------------------------------------
# _validate_semantics – logical operators ("and" / "or")
# ---------------------------------------------------------------------------


class TestLogicalOperators:
    @pytest.mark.parametrize("op", ["and", "or"])
    def test_valid_logical_with_children(self, op):
        expr = KnowledgeFilterExpr(
            operator=op,
            value=[
                {"operator": "eq", "field": "name", "value": "alice"},
                {"operator": "gt", "field": "age", "value": 18},
            ],
        )
        assert expr.operator == op
        assert expr.field == ""
        assert len(expr.value) == 2
        assert all(isinstance(c, KnowledgeFilterExpr) for c in expr.value)

    @pytest.mark.parametrize("op", ["and", "or"])
    def test_non_list_value_raises(self, op):
        with pytest.raises(ValueError, match="requires list value"):
            KnowledgeFilterExpr(operator=op, value="not a list")

    @pytest.mark.parametrize("op", ["and", "or"])
    def test_empty_children_raises(self, op):
        with pytest.raises(ValueError, match="requires non-empty child conditions"):
            KnowledgeFilterExpr(operator=op, value=[])

    @pytest.mark.parametrize("op", ["and", "or"])
    def test_none_value_raises(self, op):
        with pytest.raises(ValueError, match="requires list value"):
            KnowledgeFilterExpr(operator=op, value=None)

    def test_field_is_cleared_for_logical(self):
        expr = KnowledgeFilterExpr(
            operator="and",
            field="should_be_cleared",
            value=[{"operator": "eq", "field": "x", "value": 1}],
        )
        assert expr.field == ""

    def test_single_child(self):
        expr = KnowledgeFilterExpr(
            operator="or",
            value=[{"operator": "eq", "field": "status", "value": "done"}],
        )
        assert len(expr.value) == 1
        assert expr.value[0].operator == "eq"

    def test_children_are_validated(self):
        with pytest.raises(ValueError):
            KnowledgeFilterExpr(
                operator="and",
                value=[{"operator": "eq", "field": "", "value": "bad"}],
            )


# ---------------------------------------------------------------------------
# Nested logical expressions
# ---------------------------------------------------------------------------


class TestNestedExpressions:
    def test_nested_and_or(self):
        expr = KnowledgeFilterExpr(
            operator="and",
            value=[
                {
                    "operator": "or",
                    "value": [
                        {"operator": "eq", "field": "status", "value": "active"},
                        {"operator": "eq", "field": "status", "value": "pending"},
                    ],
                },
                {"operator": "gt", "field": "age", "value": 18},
            ],
        )
        assert expr.operator == "and"
        assert len(expr.value) == 2
        child_or = expr.value[0]
        assert child_or.operator == "or"
        assert len(child_or.value) == 2

    def test_deeply_nested(self):
        expr = KnowledgeFilterExpr(
            operator="or",
            value=[
                {
                    "operator": "and",
                    "value": [
                        {
                            "operator": "or",
                            "value": [
                                {"operator": "eq", "field": "x", "value": 1},
                                {"operator": "eq", "field": "y", "value": 2},
                            ],
                        },
                        {"operator": "gt", "field": "z", "value": 0},
                    ],
                },
            ],
        )
        assert expr.operator == "or"
        inner_and = expr.value[0]
        assert inner_and.operator == "and"
        inner_or = inner_and.value[0]
        assert inner_or.operator == "or"


# ---------------------------------------------------------------------------
# Serialization / round-trip
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_model_dump_comparison(self):
        expr = KnowledgeFilterExpr(operator="eq", field="name", value="alice")
        data = expr.model_dump()
        assert data == {"operator": "eq", "field": "name", "value": "alice"}

    def test_model_dump_logical(self):
        expr = KnowledgeFilterExpr(
            operator="and",
            value=[
                {"operator": "eq", "field": "a", "value": 1},
                {"operator": "ne", "field": "b", "value": 2},
            ],
        )
        data = expr.model_dump()
        assert data["operator"] == "and"
        assert data["field"] == ""
        assert len(data["value"]) == 2

    def test_round_trip(self):
        original = KnowledgeFilterExpr(
            operator="or",
            value=[
                {"operator": "in", "field": "tag", "value": ["a", "b"]},
                {"operator": "between", "field": "score", "value": [0.5, 1.0]},
            ],
        )
        data = original.model_dump()
        restored = KnowledgeFilterExpr.model_validate(data)
        assert restored.operator == original.operator
        assert len(restored.value) == len(original.value)
        assert restored.value[0].operator == "in"
        assert restored.value[1].operator == "between"

    def test_json_round_trip(self):
        original = KnowledgeFilterExpr(operator="eq", field="name", value="test")
        json_str = original.model_dump_json()
        restored = KnowledgeFilterExpr.model_validate_json(json_str)
        assert restored.operator == original.operator
        assert restored.field == original.field
        assert restored.value == original.value


# ---------------------------------------------------------------------------
# Default field values
# ---------------------------------------------------------------------------


class TestDefaultValues:
    def test_default_field_is_empty(self):
        expr = KnowledgeFilterExpr(
            operator="and",
            value=[{"operator": "eq", "field": "x", "value": 1}],
        )
        assert expr.field == ""

    def test_default_value_is_none_but_comparison_requires_value(self):
        with pytest.raises(ValueError, match="requires value"):
            KnowledgeFilterExpr(operator="eq", field="name")


# ---------------------------------------------------------------------------
# All comparison operators produce valid expressions
# ---------------------------------------------------------------------------


class TestAllComparisonOperatorsAccept:
    @pytest.mark.parametrize("op", ["eq", "ne", "gt", "gte", "lt", "lte", "like", "not like"])
    def test_scalar_comparison(self, op):
        expr = KnowledgeFilterExpr(operator=op, field="f", value="v")
        assert expr.operator == op

    def test_in_operator(self):
        expr = KnowledgeFilterExpr(operator="in", field="f", value=["a"])
        assert expr.operator == "in"

    def test_not_in_operator(self):
        expr = KnowledgeFilterExpr(operator="not in", field="f", value=["a"])
        assert expr.operator == "not in"

    def test_between_operator(self):
        expr = KnowledgeFilterExpr(operator="between", field="f", value=[1, 2])
        assert expr.operator == "between"
