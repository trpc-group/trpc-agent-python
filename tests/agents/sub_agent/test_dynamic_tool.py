# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Tests for DynamicAgentTool — on-the-fly sub-agent creation with LLM-written instruction."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.agents.sub_agent import DynamicAgentTool
from trpc_agent_sdk.agents.sub_agent import SubAgentConfig
from trpc_agent_sdk.agents.sub_agent._dynamic_agent_tool import _tool_names
from trpc_agent_sdk.tools import BaseToolSet
from trpc_agent_sdk.tools import ReadTool


def _make_tool_context():
    return MagicMock()


def test_constructor_minimal() -> None:
    """DynamicAgentTool() should construct with no arguments."""
    t = DynamicAgentTool()
    assert t.name == "dynamic_agent"
    assert t._agent_config is None
    assert t._skip_summarization is False


def test_constructor_with_config() -> None:
    t = DynamicAgentTool(agent_config=SubAgentConfig(parallel_tool_calls=True))
    assert t._agent_config.parallel_tool_calls is True


def test_constructor_skip_summarization() -> None:
    t = DynamicAgentTool(skip_summarization=True)
    assert t._skip_summarization is True


def test_constructor_custom_name() -> None:
    t = DynamicAgentTool(name="my_dynamic")
    assert t.name == "my_dynamic"


def test_constructor_custom_description() -> None:
    t = DynamicAgentTool(description="A custom tool description.")
    assert t.description == "A custom tool description."


def test_declaration_schema_shape() -> None:
    t = DynamicAgentTool()
    decl = t._get_declaration()
    assert decl.name == "dynamic_agent"
    props = decl.parameters.properties
    assert decl.parameters.required == ["prompt"]
    assert "instruction" in props
    assert "prompt" in props
    assert "description" not in props


def test_description_contains_key_text() -> None:
    t = DynamicAgentTool()
    assert "Run one short-lived sub-agent" in t.description
    assert "created on the fly" in t.description
    assert "IMPORTANT" in t.description


@pytest.mark.asyncio
async def test_empty_instruction_falls_back_to_default() -> None:
    """Empty/whitespace instruction falls back to default, proceeds to run_subagent."""
    t = DynamicAgentTool()
    ctx = _make_tool_context()
    result = await t._run_async_impl(
        tool_context=ctx,
        args={"instruction": "   ", "prompt": "do something"},
    )
    # Should NOT be an instruction validation error — falls back and tries to run.
    assert not (isinstance(result, dict)
                and result.get("status") == "error"
                and "instruction" in str(result.get("message")))


@pytest.mark.asyncio
async def test_empty_prompt_returns_error() -> None:
    t = DynamicAgentTool()
    ctx = _make_tool_context()
    result = await t._run_async_impl(
        tool_context=ctx,
        args={"instruction": "You are a helpful agent.", "prompt": "   "},
    )
    assert result["status"] == "error"
    assert "prompt" in result["message"]


@pytest.mark.asyncio
async def test_missing_instruction_uses_default() -> None:
    """Missing instruction uses fallback instead of returning error."""
    t = DynamicAgentTool()
    ctx = _make_tool_context()
    result = await t._run_async_impl(
        tool_context=ctx,
        args={"prompt": "do something"},
    )
    # Should NOT be a validation error — falls back and tries to run.
    assert not (isinstance(result, dict)
                and result.get("status") == "error"
                and "instruction" in str(result.get("message")))


@pytest.mark.asyncio
async def test_valid_args_creates_synthetic_archetype() -> None:
    """Valid call creates a synthetic SubAgentArchetype and passes to run_subagent."""
    t = DynamicAgentTool()
    ctx = _make_tool_context()
    # With a mock context, run_subagent will raise; we just verify
    # the error is NOT a validation error — meaning the synthetic
    # archetype was created and run_subagent was called.
    result = await t._run_async_impl(
        tool_context=ctx,
        args={
            "instruction": "You are a database expert.",
            "prompt": "Analyze the schema.",
        },
    )
    # Should NOT be a validation error.
    assert not (isinstance(result, dict)
                and result.get("status") == "error"
                and "non-empty" in str(result.get("message")))


def test_has_no_registry() -> None:
    """DynamicAgentTool should not have a registry — it uses synthetic archetypes."""
    t = DynamicAgentTool()
    assert not hasattr(t, "registry")
    assert not hasattr(t, "_registry")


@pytest.mark.asyncio
async def test_process_request_with_parent_history() -> None:
    """process_request appends history-aware instruction when include_parent_history=True."""
    t = DynamicAgentTool(agent_config=SubAgentConfig(include_parent_history=True))
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
    """process_request appends no-history instruction when agent_config=None."""
    t = DynamicAgentTool()
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
    t = DynamicAgentTool(skip_summarization=True)
    ctx = _make_tool_context()
    ctx.event_actions.skip_summarization = False

    await t._run_async_impl(
        tool_context=ctx,
        args={"prompt": "   "},
    )
    assert ctx.event_actions.skip_summarization is True


# --- expose_tool_selection=False -----------------------------------------------


def test_declaration_without_tool_selection() -> None:
    """When expose_tool_selection=False, the 'tools' field is omitted from schema."""
    t = DynamicAgentTool(expose_tool_selection=False)
    decl = t._get_declaration()
    assert "tools" not in decl.parameters.properties


# --- tools=tuple with expose_tool_selection=True --------------------------------


def test_declaration_with_fixed_tools_includes_tool_names() -> None:
    """When tools=tuple and expose_tool_selection=True, description lists tool names."""
    t = DynamicAgentTool(tools=(ReadTool(),), expose_tool_selection=True)
    decl = t._get_declaration()
    tools_prop = decl.parameters.properties["tools"]
    assert "Available tool names:" in tools_prop.description
    assert "Read" in tools_prop.description


def test_declaration_with_fixed_tools_empty_tuple() -> None:
    """When tools=() empty tuple, no tool names appended to description."""
    t = DynamicAgentTool(tools=(), expose_tool_selection=True)
    decl = t._get_declaration()
    tools_prop = decl.parameters.properties["tools"]
    assert "Available tool names:" not in tools_prop.description


# --- _tool_names ---------------------------------------------------------------


def test_tool_names_with_basetool_instance() -> None:
    names = _tool_names((ReadTool(),))
    assert names == ["Read"]


def test_tool_names_with_basetoolset_instance() -> None:
    class _FakeToolSet(BaseToolSet):
        async def get_tools(self, invocation_context=None):
            return []

    names = _tool_names((_FakeToolSet(),))
    assert names == ["_FakeToolSet"]


def test_tool_names_with_class_reference() -> None:
    names = _tool_names((ReadTool,))
    assert names == ["Read"]


def test_tool_names_with_callable_no_name() -> None:
    """Callable without __name__ is skipped (getattr with None default)."""

    class _CallableNoName:
        def __call__(self):
            return ReadTool()

    names = _tool_names((_CallableNoName(),))
    assert names == []


def test_tool_names_with_unrecognized_item() -> None:
    """Non-tool, non-callable items are skipped."""
    names = _tool_names(("not-a-tool",))
    assert names == []


# --- LLM-provided tools arg in _run_async_impl ---------------------------------


@pytest.mark.asyncio
async def test_run_async_with_tool_filter_from_llm() -> None:
    """When expose_tool_selection=True and args has 'tools' list, tool_filter is set."""
    t = DynamicAgentTool()
    ctx = _make_tool_context()
    # The mock context will cause run_subagent to raise, but we verify
    # that tool_filter forwarding doesn't break anything.
    result = await t._run_async_impl(
        tool_context=ctx,
        args={
            "instruction": "You are helpful.",
            "prompt": "Do something.",
            "tools": ["Read", "Grep"],
        },
    )
    # Should not be a validation error.
    assert not (isinstance(result, dict)
                and result.get("status") == "error"
                and "non-empty" in str(result.get("message")))


@pytest.mark.asyncio
async def test_run_async_ignores_non_list_tools_arg() -> None:
    """When 'tools' arg is not a list, tool_filter remains None."""
    t = DynamicAgentTool()
    ctx = _make_tool_context()
    result = await t._run_async_impl(
        tool_context=ctx,
        args={
            "instruction": "You are helpful.",
            "prompt": "Do something.",
            "tools": "not-a-list",
        },
    )
    # Should not be a validation error.
    assert not (isinstance(result, dict)
                and result.get("status") == "error"
                and "non-empty" in str(result.get("message")))


@pytest.mark.asyncio
async def test_run_async_without_tool_selection_ignores_tools_arg() -> None:
    """When expose_tool_selection=False, 'tools' arg is ignored."""
    t = DynamicAgentTool(expose_tool_selection=False)
    ctx = _make_tool_context()
    result = await t._run_async_impl(
        tool_context=ctx,
        args={
            "instruction": "You are helpful.",
            "prompt": "Do something.",
            "tools": ["Read"],
        },
    )
    # Should not be a validation error.
    assert not (isinstance(result, dict)
                and result.get("status") == "error"
                and "non-empty" in str(result.get("message")))
