# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.skills.tools._skill_select_tools.

Covers:
- SkillSelectToolsResult: alias field mapping
- skill_select_tools: replace, add, clear modes
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.skills.tools._skill_select_tools import (
    SkillSelectToolsResult,
    skill_select_tools,
)


def _make_ctx(state_delta=None, session_state=None):
    ctx = MagicMock()
    ctx.actions.state_delta = state_delta or {}
    ctx.session_state = session_state or {}
    return ctx


# ---------------------------------------------------------------------------
# SkillSelectToolsResult
# ---------------------------------------------------------------------------

class TestSkillSelectToolsResult:
    def test_alias_selected_items(self):
        result = SkillSelectToolsResult(
            skill="test",
            selected_items=["tool_a", "tool_b"],
            include_all=False,
        )
        assert result.selected_tools == ["tool_a", "tool_b"]
        assert result.include_all_tools is False

    def test_alias_include_all(self):
        result = SkillSelectToolsResult(
            skill="test",
            selected_items=[],
            include_all=True,
        )
        assert result.include_all_tools is True

    def test_direct_field_setting(self):
        result = SkillSelectToolsResult(
            skill="test",
            selected_tools=["t1"],
            include_all_tools=True,
        )
        assert result.selected_tools == ["t1"]


# ---------------------------------------------------------------------------
# skill_select_tools
# ---------------------------------------------------------------------------

class TestSkillSelectTools:
    def test_replace_mode(self):
        ctx = _make_ctx()
        result = skill_select_tools(ctx, "test-skill", tools=["tool_a", "tool_b"], mode="replace")
        assert result.skill == "test-skill"
        assert result.mode == "replace"
        assert result.selected_tools == ["tool_a", "tool_b"]

    def test_add_mode(self):
        ctx = _make_ctx(session_state={
            "temp:skill:tools:test-skill": json.dumps(["existing_tool"]),
        })
        result = skill_select_tools(ctx, "test-skill", tools=["new_tool"], mode="add")
        assert result.mode == "add"
        assert "existing_tool" in result.selected_tools
        assert "new_tool" in result.selected_tools

    def test_clear_mode(self):
        ctx = _make_ctx(session_state={
            "temp:skill:tools:test-skill": json.dumps(["tool"]),
        })
        result = skill_select_tools(ctx, "test-skill", mode="clear")
        assert result.mode == "clear"
        assert result.selected_tools == []

    def test_include_all_tools(self):
        ctx = _make_ctx()
        result = skill_select_tools(ctx, "test-skill", include_all_tools=True, mode="replace")
        assert result.include_all_tools is True

    def test_updates_state_delta(self):
        ctx = _make_ctx()
        skill_select_tools(ctx, "test-skill", tools=["t1"], mode="replace")
        key = "temp:skill:tools:test-skill"
        assert key in ctx.actions.state_delta
