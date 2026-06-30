# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Tests for SubAgentRegistry."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.agents.sub_agent import SubAgentArchetype
from trpc_agent_sdk.agents.sub_agent import SubAgentRegistry
from trpc_agent_sdk.tools import ReadTool


def _arc(name: str) -> SubAgentArchetype:
    return SubAgentArchetype(
        name=name,
        description=f"archetype {name}",
        instruction="be helpful",
        tools=(ReadTool,),
    )


def test_register_and_get() -> None:
    reg = SubAgentRegistry()
    a = _arc("alpha")
    reg.register(a)
    assert reg.get("alpha") is a
    assert "alpha" in reg
    assert len(reg) == 1


def test_insertion_order_preserved() -> None:
    reg = SubAgentRegistry()
    for n in ["zeta", "alpha", "mu"]:
        reg.register(_arc(n))
    assert reg.names() == ["zeta", "alpha", "mu"]


def test_duplicate_registration_rejected() -> None:
    reg = SubAgentRegistry()
    reg.register(_arc("alpha"))
    with pytest.raises(ValueError):
        reg.register(_arc("alpha"))


def test_missing_get_raises() -> None:
    reg = SubAgentRegistry()
    with pytest.raises(KeyError):
        reg.get("nope")


def test_archetypes_returns_in_order() -> None:
    reg = SubAgentRegistry()
    a = _arc("a")
    b = _arc("b")
    reg.register(a)
    reg.register(b)
    assert reg.archetypes() == [a, b]


def test_iter_yields_archetypes() -> None:
    reg = SubAgentRegistry()
    items = [_arc("x"), _arc("y")]
    for it in items:
        reg.register(it)
    assert list(reg) == items


def test_contains_only_string_keys() -> None:
    reg = SubAgentRegistry()
    reg.register(_arc("hello"))
    assert "hello" in reg
    assert 123 not in reg  # type: ignore[operator]
