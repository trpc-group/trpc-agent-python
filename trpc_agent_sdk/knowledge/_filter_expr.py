# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unified filter model for knowledge backends."""

from __future__ import annotations

from typing import Any
from typing import Literal
from typing import TypeAlias

from pydantic import BaseModel
from pydantic import model_validator

_LOGICAL_OPERATORS = {"and", "or"}
_COMPARISON_OPERATORS = {
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "not in",
    "like",
    "not like",
    "between",
}
_ALL_OPERATORS = _LOGICAL_OPERATORS | _COMPARISON_OPERATORS

KnowledgeFilterOperator: TypeAlias = Literal[
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "not in",
    "like",
    "not like",
    "between",
    "and",
    "or",
]


class KnowledgeFilterExpr(BaseModel):
    """Typed knowledge filter expression."""

    operator: KnowledgeFilterOperator
    field: str = ""
    value: Any = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_operator(cls, data: Any) -> Any:
        if isinstance(data, KnowledgeFilterExpr):
            return data
        if not isinstance(data, dict):
            raise ValueError(f"knowledge filter must be an object, got {type(data)}")

        operator_raw = data.get("operator")
        if not isinstance(operator_raw, str):
            raise ValueError("knowledge filter operator must be a string")
        operator = operator_raw.strip().lower()
        if operator not in _ALL_OPERATORS:
            raise ValueError(
                f"unsupported knowledge filter operator: {operator!r}, "
                "supported: eq, ne, gt, gte, lt, lte, in, not in, like, not like, between, and, or", )

        normalized_data = dict(data)
        normalized_data["operator"] = operator
        return normalized_data

    @model_validator(mode="after")
    def _validate_semantics(self) -> KnowledgeFilterExpr:
        operator = self.operator
        value = self.value

        if operator in _LOGICAL_OPERATORS:
            if not isinstance(value, list):
                raise ValueError(f"logical operator {operator!r} requires list value")
            children = [KnowledgeFilterExpr.model_validate(item) for item in value]
            if not children:
                raise ValueError(f"logical operator {operator!r} requires non-empty child conditions")
            self.field = ""
            self.value = children
            return self

        if self.field.strip() == "":
            raise ValueError(f"comparison operator {operator!r} requires non-empty field")
        self.field = self.field.strip()

        if operator in {"in", "not in"}:
            if not isinstance(value, list) or len(value) == 0:
                raise ValueError(f"operator {operator!r} requires non-empty list value")
        elif operator == "between":
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError("operator 'between' requires list value with exactly two items")
        elif value is None:
            raise ValueError(f"comparison operator {operator!r} requires value")

        return self
