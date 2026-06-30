# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Tests for the dynamic_agent tool description rendering."""

from __future__ import annotations

from trpc_agent_sdk.agents.sub_agent import EXPLORE_AGENT
from trpc_agent_sdk.agents.sub_agent import GENERAL_PURPOSE_AGENT
from trpc_agent_sdk.agents.sub_agent import SubAgentArchetype
from trpc_agent_sdk.agents.sub_agent import SubAgentRegistry
from trpc_agent_sdk.agents.sub_agent._description import render_archetype_block
from trpc_agent_sdk.agents.sub_agent._description import render_tool_description
from trpc_agent_sdk.agents.sub_agent._description import tool_names_of
from trpc_agent_sdk.tools import GrepTool
from trpc_agent_sdk.tools import ReadTool


def test_tool_names_from_class_refs() -> None:
    assert tool_names_of(EXPLORE_AGENT) == ["Read", "Glob", "Grep", "webfetch"]


def test_tool_names_from_instances() -> None:
    arc = SubAgentArchetype(
        name="custom",
        description="d",
        instruction="i",
        tools=(ReadTool(), GrepTool()),
    )
    names = tool_names_of(arc)
    # BaseTool instances expose `.name`, which differs from the class name.
    assert len(names) == 2
    assert all(isinstance(n, str) and n for n in names)


def test_archetype_block_includes_tools_suffix() -> None:
    block = render_archetype_block(EXPLORE_AGENT)
    assert block.startswith("- Explore:")
    assert "(Tools: Read, Glob, Grep, webfetch)" in block


def test_full_render_contains_all_archetypes() -> None:
    reg = SubAgentRegistry()
    reg.register(GENERAL_PURPOSE_AGENT)
    reg.register(EXPLORE_AGENT)
    out = render_tool_description(reg)
    assert "Available subagent types:" in out
    assert "- general-purpose:" in out
    assert "- Explore:" in out
    assert "IMPORTANT:" in out


def test_render_is_deterministic() -> None:
    reg = SubAgentRegistry()
    reg.register(GENERAL_PURPOSE_AGENT)
    reg.register(EXPLORE_AGENT)
    assert render_tool_description(reg) == render_tool_description(reg)


def test_archetype_with_no_tools() -> None:
    arc = SubAgentArchetype(
        name="bare",
        description="d",
        instruction="i",
        tools=(),
    )
    block = render_archetype_block(arc)
    assert "(Tools: (none))" in block


def test_tool_names_of_none_tools() -> None:
    arc = SubAgentArchetype(name="inherit", description="d", instruction="i", tools=None)
    assert tool_names_of(arc) == ["(all)"]


def test_tool_names_of_toolset_instance() -> None:
    from trpc_agent_sdk.tools import BaseToolSet

    class _FakeToolSet(BaseToolSet):
        async def get_tools(self, invocation_context=None):
            return []

    toolset = _FakeToolSet()
    arc = SubAgentArchetype(name="ts", description="d", instruction="i", tools=(toolset,))
    assert tool_names_of(arc) == ["_FakeToolSet"]


def test_tool_names_of_plain_callable() -> None:
    def my_custom_tool():
        return ReadTool()

    arc = SubAgentArchetype(name="call", description="d", instruction="i", tools=(my_custom_tool,))
    assert tool_names_of(arc) == ["my_custom_tool"]


def test_render_archetype_block_tools_none() -> None:
    arc = SubAgentArchetype(name="gp", description="GP agent.", instruction="i", tools=None)
    block = render_archetype_block(arc)
    assert "(Tools: (all))" in block


def test_tool_names_of_unrecognized_item() -> None:
    """Non-tool, non-class, non-callable items fall back to type name."""
    arc = SubAgentArchetype(name="odd", description="d", instruction="i", tools=(42,))
    assert tool_names_of(arc) == ["int"]


def test_tool_names_of_callable_without_name() -> None:
    """Callable without __name__ falls back to repr."""
    arc = SubAgentArchetype(name="odd", description="d", instruction="i", tools=(lambda x: x,))
    names = tool_names_of(arc)
    # lambda has no __name__, so repr(t) is used.
    assert len(names) == 1
    assert "lambda" in names[0]
