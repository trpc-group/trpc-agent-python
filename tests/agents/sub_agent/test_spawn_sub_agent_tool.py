# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Tests for SpawnSubAgentTool — catalog-based sub-agent dispatch."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.agents.sub_agent import SpawnSubAgentTool
from trpc_agent_sdk.agents.sub_agent import DEFAULT_AGENT
from trpc_agent_sdk.agents.sub_agent import EXPLORE_AGENT
from trpc_agent_sdk.agents.sub_agent import GENERAL_PURPOSE_AGENT
from trpc_agent_sdk.agents.sub_agent import PLAN_AGENT
from trpc_agent_sdk.agents.sub_agent import SubAgentArchetype
from trpc_agent_sdk.agents.sub_agent import SubAgentConfig
from trpc_agent_sdk.tools import ReadTool


def _custom_archetype(name: str = "custom") -> SubAgentArchetype:
    return SubAgentArchetype(
        name=name,
        description=f"a custom archetype {name}",
        instruction="be helpful",
        tools=(ReadTool,),
    )


def _make_tool_context():
    return MagicMock()


def test_default_construction_registers_default() -> None:
    t = SpawnSubAgentTool()
    assert t.registry.names() == ["default"]


def test_agents_appended() -> None:
    t = SpawnSubAgentTool(agents=[_custom_archetype()])
    assert t.registry.names() == ["default", "custom"]


def test_agent_name_collision_rejected() -> None:
    with pytest.raises(ValueError, match="collides"):
        SpawnSubAgentTool(agents=[_custom_archetype("default")])


def test_general_purpose_is_not_auto_registered() -> None:
    """``general-purpose`` is opt-in via ``agents=[GENERAL_PURPOSE_AGENT]``."""
    t = SpawnSubAgentTool()
    assert "general-purpose" not in t.registry.names()


def test_general_purpose_can_be_added_explicitly() -> None:
    t = SpawnSubAgentTool(agents=[GENERAL_PURPOSE_AGENT])
    assert t.registry.names() == ["default", "general-purpose"]


def test_agent_paths_appended(tmp_path) -> None:
    md = tmp_path / "explorer.md"
    md.write_text(
        "---\nname: explorer\ndescription: An explorer agent.\n---\n\nExplore."
    )
    t = SpawnSubAgentTool(agent_paths=[tmp_path])
    assert t.registry.names() == ["default", "explorer"]


def test_agent_paths_collision_raises(tmp_path) -> None:
    md = tmp_path / "clash.md"
    md.write_text(
        "---\nname: default\ndescription: Collides with built-in.\n---\n\nClash."
    )
    with pytest.raises(ValueError, match="collides"):
        SpawnSubAgentTool(agent_paths=[tmp_path])


def test_with_default_false_is_empty() -> None:
    t = SpawnSubAgentTool(with_default=False)
    assert t.registry.names() == []


def test_with_default_false_with_agents(tmp_path) -> None:
    t = SpawnSubAgentTool(agents=[_custom_archetype()], with_default=False)
    assert t.registry.names() == ["custom"]


def test_with_default_false_with_agent_paths(tmp_path) -> None:
    md = tmp_path / "explorer.md"
    md.write_text(
        "---\nname: explorer\ndescription: An explorer agent.\n---\n\nExplore."
    )
    t = SpawnSubAgentTool(agent_paths=[tmp_path], with_default=False)
    assert t.registry.names() == ["explorer"]


def test_declaration_schema_shape() -> None:
    t = SpawnSubAgentTool(agents=[_custom_archetype()])
    decl = t._get_declaration()
    assert decl.name == "spawn_subagent"
    props = decl.parameters.properties
    assert set(decl.parameters.required) == {"prompt", "description"}
    assert props["subagent_type"].enum == ["default", "custom"]


def test_description_contains_default() -> None:
    t = SpawnSubAgentTool()
    assert "- default:" in t.description


def test_explicit_defaults_can_register_all_four() -> None:
    t = SpawnSubAgentTool(
        agents=[GENERAL_PURPOSE_AGENT, EXPLORE_AGENT, PLAN_AGENT],
        with_default=False,
    )
    assert t.registry.names() == ["general-purpose", "Explore", "Plan"]


@pytest.mark.asyncio
async def test_unknown_subagent_type_returns_error_when_no_default() -> None:
    t = SpawnSubAgentTool(with_default=False)
    ctx = _make_tool_context()
    result = await t._run_async_impl(
        tool_context=ctx,
        args={"subagent_type": "nope", "prompt": "hi", "description": "x"},
    )
    assert result["status"] == "error"
    assert "unknown subagent_type" in result["message"]


@pytest.mark.asyncio
async def test_missing_subagent_type_falls_back_to_default() -> None:
    t = SpawnSubAgentTool()
    ctx = _make_tool_context()
    result = await t._run_async_impl(
        tool_context=ctx,
        args={"prompt": "hi", "description": "x"},
    )
    # Falls back to default, tries to run sub-agent.
    # Since ctx is a mock, it will raise an error from run_subagent,
    # but it should NOT be the "unknown subagent_type" error.
    assert not (
        isinstance(result, dict)
        and result.get("status") == "error"
        and "unknown subagent_type" in str(result.get("message"))
    )


@pytest.mark.asyncio
async def test_empty_prompt_returns_error() -> None:
    t = SpawnSubAgentTool()
    ctx = _make_tool_context()
    result = await t._run_async_impl(
        tool_context=ctx,
        args={"subagent_type": "default", "prompt": "   ", "description": "x"},
    )
    assert result["status"] == "error"
    assert "non-empty" in result["message"]


def test_default_agent_tools_is_none() -> None:
    """DEFAULT_AGENT.tools should be None (inherit parent tools)."""
    assert DEFAULT_AGENT.tools is None


def test_archetype_tools_none_ok() -> None:
    """SubAgentArchetype should accept tools=None."""
    a = SubAgentArchetype(
        name="test-none",
        description="tools=None archetype",
        instruction="be helpful",
        tools=None,
    )
    assert a.tools is None


def test_description_shows_all_for_none_tools() -> None:
    """When tools=None, the description should show (Tools: (all))."""
    t = SpawnSubAgentTool()
    assert "(Tools: (all))" in t.description


def test_tool_mapping_custom_tool_in_md(tmp_path) -> None:
    """MD-defined archetype with a custom tool resolved via tool_mapping."""
    md = tmp_path / "custom.md"
    md.write_text(
        "---\nname: custom\ndescription: Custom tool.\ntools:\n  - MyTool\n---\n\nBe helpful."
    )
    t = SpawnSubAgentTool(agent_paths=[tmp_path], tool_mapping={"MyTool": ReadTool})
    archetype = t.registry.get("custom")
    assert archetype is not None
    assert archetype.tools == (ReadTool,)


def test_tool_mapping_unknown_in_md_still_errors(tmp_path) -> None:
    """Unknown tool name raises ValueError even with unrelated tool_mapping."""
    md = tmp_path / "bad.md"
    md.write_text(
        "---\nname: bad\ndescription: Bad.\ntools:\n  - NotReal\n---\n\nBody."
    )
    with pytest.raises(ValueError, match="unknown tool"):
        SpawnSubAgentTool(agent_paths=[tmp_path], tool_mapping={"MyTool": ReadTool})


def test_md_archetype_no_tools_inherits(tmp_path) -> None:
    """MD-defined archetype without tools: should get tools=None."""
    md = tmp_path / "explorer.md"
    md.write_text(
        "---\nname: explorer\ndescription: No tools specified.\n---\n\nExplore stuff."
    )
    t = SpawnSubAgentTool(agent_paths=[tmp_path])
    archetype = t.registry.get("explorer")
    assert archetype is not None
    assert archetype.tools is None


def test_agent_config_accepted_by_constructor() -> None:
    """SpawnSubAgentTool accepts SubAgentConfig without error."""
    t = SpawnSubAgentTool(agent_config=SubAgentConfig(parallel_tool_calls=True))
    assert t._agent_config.parallel_tool_calls is True


@pytest.mark.asyncio
async def test_process_request_with_parent_history() -> None:
    """process_request appends history-aware instruction when include_parent_history=True."""
    t = SpawnSubAgentTool(agent_config=SubAgentConfig(include_parent_history=True))
    llm_request = MagicMock()
    llm_request.append_instructions = MagicMock()
    ctx = _make_tool_context()

    await t.process_request(tool_context=ctx, llm_request=llm_request)

    llm_request.append_instructions.assert_called_once()
    instruction = llm_request.append_instructions.call_args[0][0][0]
    assert "can see the" in instruction
    assert "current conversation" in instruction


@pytest.mark.asyncio
async def test_process_request_without_parent_history() -> None:
    """process_request appends no-history instruction when include_parent_history=False."""
    t = SpawnSubAgentTool()
    llm_request = MagicMock()
    llm_request.append_instructions = MagicMock()
    ctx = _make_tool_context()

    await t.process_request(tool_context=ctx, llm_request=llm_request)

    llm_request.append_instructions.assert_called_once()
    instruction = llm_request.append_instructions.call_args[0][0][0]
    assert "has no memory" in instruction


@pytest.mark.asyncio
async def test_skip_summarization_sets_event_action() -> None:
    """When skip_summarization=True, _run_async_impl sets skip_summarization on event_actions."""
    t = SpawnSubAgentTool(with_default=False, skip_summarization=True)
    ctx = _make_tool_context()
    ctx.event_actions.skip_summarization = False

    await t._run_async_impl(
        tool_context=ctx,
        args={"subagent_type": "nope", "prompt": "hi", "description": "x"},
    )
    assert ctx.event_actions.skip_summarization is True
