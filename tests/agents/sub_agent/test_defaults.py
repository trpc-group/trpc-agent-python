# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Tests for the built-in default archetypes."""

from __future__ import annotations

from trpc_agent_sdk.agents.sub_agent import DEFAULT_AGENT
from trpc_agent_sdk.agents.sub_agent import EXPLORE_AGENT
from trpc_agent_sdk.agents.sub_agent import GENERAL_PURPOSE_AGENT
from trpc_agent_sdk.agents.sub_agent import PLAN_AGENT
from trpc_agent_sdk.tools import BashTool
from trpc_agent_sdk.tools import EditTool
from trpc_agent_sdk.tools import GlobTool
from trpc_agent_sdk.tools import GrepTool
from trpc_agent_sdk.tools import ReadTool
from trpc_agent_sdk.tools import WebFetchTool
from trpc_agent_sdk.tools import WriteTool


def test_archetype_names() -> None:
    assert DEFAULT_AGENT.name == "default"
    assert GENERAL_PURPOSE_AGENT.name == "general-purpose"
    assert EXPLORE_AGENT.name == "Explore"
    assert PLAN_AGENT.name == "Plan"


def test_default_tools_is_none() -> None:
    """DEFAULT_AGENT.tools should be None — it inherits parent tools at spawn time."""
    assert DEFAULT_AGENT.tools is None


def test_general_purpose_tools_is_none() -> None:
    """GENERAL_PURPOSE_AGENT.tools should be None — it inherits parent tools at spawn time."""
    assert GENERAL_PURPOSE_AGENT.tools is None


def test_explore_is_read_only() -> None:
    assert EXPLORE_AGENT.tools == (ReadTool, GlobTool, GrepTool, WebFetchTool)
    assert WriteTool not in EXPLORE_AGENT.tools
    assert EditTool not in EXPLORE_AGENT.tools
    assert BashTool not in EXPLORE_AGENT.tools


def test_plan_is_read_only_no_web() -> None:
    assert PLAN_AGENT.tools == (ReadTool, GlobTool, GrepTool)


def test_description_does_not_preinclude_tools_suffix() -> None:
    """The renderer is responsible for appending '(Tools: ...)' — description must not."""
    for arc in (DEFAULT_AGENT, GENERAL_PURPOSE_AGENT, EXPLORE_AGENT, PLAN_AGENT):
        assert "(Tools:" not in arc.description, (f"archetype {arc.name!r} pre-includes Tools suffix in description; "
                                                  "the renderer is supposed to add it")


def test_explore_and_plan_instructions_share_readonly_preamble() -> None:
    """Both read-only archetypes carry the CRITICAL READ-ONLY preamble."""
    assert "CRITICAL: You are in READ-ONLY mode" in EXPLORE_AGENT.instruction
    assert "CRITICAL: You are in READ-ONLY mode" in PLAN_AGENT.instruction


def test_default_instruction_is_neutral() -> None:
    """DEFAULT_AGENT's instruction does not impose a specific role.

    Unlike GENERAL_PURPOSE_AGENT which is shaped as a researcher with
    'search broadly first / never create files' biases, DEFAULT_AGENT
    intentionally leaves the role to the task at hand.
    """
    instr = DEFAULT_AGENT.instruction
    assert "READ-ONLY mode" not in instr
    # No researcher-specific role framing.
    assert "research" not in instr.lower()
    assert "Search broadly" not in instr
    # No persona-style "## Strengths" / "## Guidelines" sections.
    assert "## Strengths" not in instr


def test_general_purpose_instruction_is_not_read_only() -> None:
    assert "READ-ONLY mode" not in GENERAL_PURPOSE_AGENT.instruction


def test_default_models_are_unset() -> None:
    """Defaults have model=None so they inherit SubAgentConfig.model / parent.model."""
    for arc in (DEFAULT_AGENT, GENERAL_PURPOSE_AGENT, EXPLORE_AGENT, PLAN_AGENT):
        assert arc.model is None
