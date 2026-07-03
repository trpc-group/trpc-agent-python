# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Tests for SubAgentArchetype validation and tool-list handling."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.agents.sub_agent import SubAgentArchetype
from trpc_agent_sdk.tools import ReadTool


def _make(**overrides) -> SubAgentArchetype:
    base = dict(
        name="my_archetype",
        description="A useful description.",
        instruction="Be helpful.",
        tools=(ReadTool,),
    )
    base.update(overrides)
    return SubAgentArchetype(**base)


def test_construct_with_factory_tools() -> None:
    a = _make()
    assert a.name == "my_archetype"
    assert a.tools == (ReadTool,)


def test_construct_with_instance_tools() -> None:
    inst = ReadTool()
    a = _make(tools=(inst,))
    assert a.tools == (inst,)


def test_tools_coerced_to_tuple() -> None:
    a = _make(tools=[ReadTool])
    assert isinstance(a.tools, tuple)


def test_reject_empty_name() -> None:
    with pytest.raises(ValueError):
        _make(name="")


def test_reject_invalid_name() -> None:
    with pytest.raises(ValueError):
        _make(name="bad name with spaces")


def test_reject_empty_description() -> None:
    with pytest.raises(ValueError):
        _make(description="   ")


def test_reject_empty_instruction() -> None:
    with pytest.raises(ValueError):
        _make(instruction="   ")


def test_accepts_claude_code_style_names() -> None:
    """Names like 'general-purpose' and 'Explore' must validate."""
    _make(name="general-purpose")
    _make(name="Explore")
    _make(name="claude-code-guide")


def test_frozen_dataclass_is_immutable() -> None:
    a = _make()
    with pytest.raises(Exception):
        a.name = "renamed"


def test_model_or_returns_self_when_set() -> None:
    a = SubAgentArchetype(
        name="with-model",
        description="Has a model.",
        instruction="Be helpful.",
        model="my_model",
    )
    assert a.model_or("fallback") == "my_model"


def test_model_or_returns_fallback_when_none() -> None:
    a = _make()
    assert a.model is None
    assert a.model_or("fallback") == "fallback"


def test_callable_instruction_accepted() -> None:
    def dynamic_instruction(ctx):
        return "instruction from callable"

    a = SubAgentArchetype(
        name="callable-instr",
        description="Uses callable instruction.",
        instruction=dynamic_instruction,
    )
    assert a.instruction is dynamic_instruction
