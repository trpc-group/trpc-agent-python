# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Bounded abstract values shared by language analyzers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ValueState(str, Enum):
    """Static knowledge available for one analyzed value."""

    KNOWN = "known"
    CAPABILITY = "capability"
    TAINTED = "tainted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AbstractValue:
    """Small immutable value lattice used during bounded analysis."""

    state: ValueState
    value: Any = None
    reason: str | None = None

    @classmethod
    def known(cls, value: Any) -> "AbstractValue":
        return cls(ValueState.KNOWN, value=value)

    @classmethod
    def capability(cls, capability_id: str) -> "AbstractValue":
        return cls(ValueState.CAPABILITY, value=capability_id)

    @classmethod
    def tainted(cls, source_kind: str) -> "AbstractValue":
        return cls(ValueState.TAINTED, value=source_kind)

    @classmethod
    def unknown(cls, reason: str) -> "AbstractValue":
        return cls(ValueState.UNKNOWN, reason=reason)

    def merge(self, other: "AbstractValue") -> "AbstractValue":
        """Join two control-flow values without inventing certainty."""

        if self == other:
            return self
        if self.state == ValueState.TAINTED:
            return self
        if other.state == ValueState.TAINTED:
            return other
        return AbstractValue.unknown("control-flow values disagree")
