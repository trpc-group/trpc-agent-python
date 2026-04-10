# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.skills._dynamic_toolset.

Covers:
- DynamicSkillToolSet initialization (tools, toolsets, string names)
- _find_tool_by_name / _find_tool_by_type
- _resolve_tool: cache, available_tools, toolsets, global registry
- get_tools: active skills, loaded skills, tool selection
- _get_loaded_skills_from_state / _get_active_skills_from_delta
- _get_tools_selection: JSON, star, fallback to defaults
- _get_skill_default_tools
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from trpc_agent_sdk.skills._dynamic_toolset import DynamicSkillToolSet
from trpc_agent_sdk.skills._common import loaded_state_key
from trpc_agent_sdk.skills._common import tool_state_key
from trpc_agent_sdk.skills._constants import SKILL_CONFIG_KEY
from trpc_agent_sdk.skills._skill_config import DEFAULT_SKILL_CONFIG


def _make_ctx(state_delta=None, session_state=None):
    ctx = MagicMock()
    ctx.actions.state_delta = state_delta or {}
    ctx.session_state = session_state or {}
    ctx.agent_name = ""
    ctx.agent_context.get_metadata = MagicMock(
        side_effect=lambda key, default=None: DEFAULT_SKILL_CONFIG if key == SKILL_CONFIG_KEY else default)
    return ctx


def _make_mock_tool(name: str):
    tool = MagicMock()
    tool.name = name
    return tool


def _make_mock_toolset(name: str, tools=None):
    toolset = MagicMock()
    toolset.name = name
    toolset.get_tools = AsyncMock(return_value=tools or [])
    return toolset


def _make_mock_repository(skills=None):
    repo = MagicMock()
    skills = skills or {}
    def _get(name):
        if name in skills:
            return skills[name]
        raise ValueError(f"skill '{name}' not found")
    repo.get = _get
    return repo


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

class TestDynamicSkillToolSetInit:
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    def test_init_no_tools(self, mock_get_tool_set, mock_get_tool):
        repo = _make_mock_repository()
        ts = DynamicSkillToolSet(skill_repository=repo)
        assert ts._only_active_skills is True
        assert len(ts._available_tools) == 0

    def test_init_with_tool_instances(self):
        repo = _make_mock_repository()
        tool = _make_mock_tool("my_tool")
        # BaseTool check
        with patch("trpc_agent_sdk.skills._dynamic_toolset.isinstance", side_effect=isinstance):
            ts = DynamicSkillToolSet(skill_repository=repo, available_tools=[tool])

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool")
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set")
    def test_init_with_string_tool_found(self, mock_get_tool_set, mock_get_tool):
        repo = _make_mock_repository()
        mock_tool = _make_mock_tool("found_tool")
        mock_get_tool.return_value = mock_tool
        ts = DynamicSkillToolSet(skill_repository=repo, available_tools=["found_tool"])
        assert "found_tool" in ts._available_tools

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set")
    def test_init_with_string_toolset_found(self, mock_get_tool_set, mock_get_tool):
        repo = _make_mock_repository()
        mock_ts = _make_mock_toolset("found_toolset")
        mock_get_tool_set.return_value = mock_ts
        ts = DynamicSkillToolSet(skill_repository=repo, available_tools=["found_toolset"])
        assert len(ts._available_toolsets) == 1


# ---------------------------------------------------------------------------
# _get_loaded_skills_from_state
# ---------------------------------------------------------------------------

class TestGetLoadedSkillsFromState:
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    def test_no_loaded_skills(self, *_):
        repo = _make_mock_repository()
        ts = DynamicSkillToolSet(skill_repository=repo)
        ctx = _make_ctx()
        assert ts._get_loaded_skills_from_state(ctx) == []

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    def test_loaded_skills_from_session_and_delta(self, *_):
        repo = _make_mock_repository()
        ts = DynamicSkillToolSet(skill_repository=repo)
        ctx = _make_ctx()
        ctx.session_state = {loaded_state_key(ctx, "skill-a"): True}
        ctx.actions.state_delta = {loaded_state_key(ctx, "skill-b"): True}
        result = ts._get_loaded_skills_from_state(ctx)
        assert set(result) == {"skill-a", "skill-b"}


# ---------------------------------------------------------------------------
# _get_active_skills_from_delta
# ---------------------------------------------------------------------------

class TestGetActiveSkillsFromDelta:
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    def test_no_active_skills(self, *_):
        repo = _make_mock_repository()
        ts = DynamicSkillToolSet(skill_repository=repo)
        ctx = _make_ctx()
        assert ts._get_active_skills_from_delta(ctx) == []

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    def test_active_from_loaded(self, *_):
        repo = _make_mock_repository()
        ts = DynamicSkillToolSet(skill_repository=repo)
        ctx = _make_ctx(state_delta={loaded_state_key(_make_ctx(), "s1"): True})
        result = ts._get_active_skills_from_delta(ctx)
        assert "s1" in result

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    def test_active_from_tools_modified(self, *_):
        repo = _make_mock_repository()
        ts = DynamicSkillToolSet(skill_repository=repo)
        ctx = _make_ctx(state_delta={tool_state_key(_make_ctx(), "s2"): json.dumps(["t1"])})
        result = ts._get_active_skills_from_delta(ctx)
        assert "s2" in result

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    def test_falsy_loaded_value_ignored(self, *_):
        repo = _make_mock_repository()
        ts = DynamicSkillToolSet(skill_repository=repo)
        ctx = _make_ctx(state_delta={loaded_state_key(_make_ctx(), "s1"): False})
        assert ts._get_active_skills_from_delta(ctx) == []


# ---------------------------------------------------------------------------
# _get_tools_selection
# ---------------------------------------------------------------------------

class TestGetToolsSelection:
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    def test_json_array(self, *_):
        skill = MagicMock()
        skill.tools = ["default_tool"]
        repo = _make_mock_repository({"s1": skill})
        ts = DynamicSkillToolSet(skill_repository=repo)
        ctx = _make_ctx(state_delta={tool_state_key(_make_ctx(), "s1"): json.dumps(["tool_a", "tool_b"])})
        result = ts._get_tools_selection(ctx, "s1")
        assert result == ["tool_a", "tool_b"]

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    def test_star_returns_defaults(self, *_):
        skill = MagicMock()
        skill.tools = ["default_tool"]
        repo = _make_mock_repository({"s1": skill})
        ts = DynamicSkillToolSet(skill_repository=repo)
        ctx = _make_ctx(state_delta={tool_state_key(_make_ctx(), "s1"): "*"})
        result = ts._get_tools_selection(ctx, "s1")
        assert result == ["default_tool"]

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    def test_no_selection_falls_back_to_defaults(self, *_):
        skill = MagicMock()
        skill.tools = ["fallback_tool"]
        repo = _make_mock_repository({"s1": skill})
        ts = DynamicSkillToolSet(skill_repository=repo)
        ctx = _make_ctx()
        result = ts._get_tools_selection(ctx, "s1")
        assert result == ["fallback_tool"]

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    def test_invalid_json_falls_back(self, *_):
        skill = MagicMock()
        skill.tools = ["fallback"]
        repo = _make_mock_repository({"s1": skill})
        ts = DynamicSkillToolSet(skill_repository=repo)
        ctx = _make_ctx(state_delta={tool_state_key(_make_ctx(), "s1"): "not_json"})
        result = ts._get_tools_selection(ctx, "s1")
        assert result == ["fallback"]


# ---------------------------------------------------------------------------
# _get_skill_default_tools
# ---------------------------------------------------------------------------

class TestGetSkillDefaultTools:
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    def test_returns_skill_tools(self, *_):
        skill = MagicMock()
        skill.tools = ["t1", "t2"]
        repo = _make_mock_repository({"s1": skill})
        ts = DynamicSkillToolSet(skill_repository=repo)
        assert ts._get_skill_default_tools("s1") == ["t1", "t2"]

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    def test_skill_not_found_returns_empty(self, *_):
        repo = _make_mock_repository()
        ts = DynamicSkillToolSet(skill_repository=repo)
        assert ts._get_skill_default_tools("nonexistent") == []


# ---------------------------------------------------------------------------
# _resolve_tool
# ---------------------------------------------------------------------------

class TestResolveTool:
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    async def test_resolve_from_available_tools(self, *_):
        repo = _make_mock_repository()
        ts = DynamicSkillToolSet(skill_repository=repo)
        mock_tool = _make_mock_tool("my_tool")
        ts._available_tools["my_tool"] = mock_tool
        result = await ts._resolve_tool("my_tool")
        assert result is mock_tool

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    async def test_resolve_from_cache(self, *_):
        repo = _make_mock_repository()
        ts = DynamicSkillToolSet(skill_repository=repo)
        mock_tool = _make_mock_tool("cached_tool")
        ts._tool_cache["cached_tool"] = mock_tool
        result = await ts._resolve_tool("cached_tool")
        assert result is mock_tool

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    async def test_resolve_not_found_returns_none(self, *_):
        repo = _make_mock_repository()
        ts = DynamicSkillToolSet(skill_repository=repo)
        result = await ts._resolve_tool("nonexistent")
        assert result is None

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool")
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    async def test_resolve_from_global_registry(self, _, mock_get_tool):
        repo = _make_mock_repository()
        ts = DynamicSkillToolSet(skill_repository=repo)
        global_tool = _make_mock_tool("global_tool")
        mock_get_tool.return_value = global_tool
        result = await ts._resolve_tool("global_tool")
        assert result is global_tool


# ---------------------------------------------------------------------------
# get_tools
# ---------------------------------------------------------------------------

class TestGetTools:
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    async def test_no_skills_returns_empty(self, *_):
        repo = _make_mock_repository()
        ts = DynamicSkillToolSet(skill_repository=repo)
        ctx = _make_ctx()
        result = await ts.get_tools(ctx)
        assert result == []

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    async def test_active_skills_with_tools(self, *_):
        skill = MagicMock()
        skill.tools = ["my_tool"]
        repo = _make_mock_repository({"s1": skill})
        ts = DynamicSkillToolSet(skill_repository=repo)
        mock_tool = _make_mock_tool("my_tool")
        ts._available_tools["my_tool"] = mock_tool

        ctx = _make_ctx(state_delta={loaded_state_key(_make_ctx(), "s1"): True})
        result = await ts.get_tools(ctx)
        assert len(result) == 1
        assert result[0] is mock_tool

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    async def test_only_active_false_uses_all_loaded(self, *_):
        skill = MagicMock()
        skill.tools = ["tool_a"]
        repo = _make_mock_repository({"s1": skill})
        ts = DynamicSkillToolSet(skill_repository=repo, only_active_skills=False)
        mock_tool = _make_mock_tool("tool_a")
        ts._available_tools["tool_a"] = mock_tool

        ctx = _make_ctx(session_state={loaded_state_key(_make_ctx(), "s1"): True})
        result = await ts.get_tools(ctx)
        assert len(result) == 1

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    async def test_deduplicates_tools(self, *_):
        skill_a = MagicMock()
        skill_a.tools = ["shared_tool"]
        skill_b = MagicMock()
        skill_b.tools = ["shared_tool"]
        repo = _make_mock_repository({"s1": skill_a, "s2": skill_b})
        ts = DynamicSkillToolSet(skill_repository=repo, only_active_skills=False)
        mock_tool = _make_mock_tool("shared_tool")
        ts._available_tools["shared_tool"] = mock_tool

        key_ctx = _make_ctx()
        ctx = _make_ctx(session_state={
            loaded_state_key(key_ctx, "s1"): True,
            loaded_state_key(key_ctx, "s2"): True,
        })
        result = await ts.get_tools(ctx)
        assert len(result) == 1

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    async def test_fallback_to_loaded_when_no_active(self, *_):
        skill = MagicMock()
        skill.tools = ["fallback_tool"]
        repo = _make_mock_repository({"s1": skill})
        ts = DynamicSkillToolSet(skill_repository=repo, only_active_skills=True)
        mock_tool = _make_mock_tool("fallback_tool")
        ts._available_tools["fallback_tool"] = mock_tool

        ctx = _make_ctx(session_state={loaded_state_key(_make_ctx(), "s1"): True})
        result = await ts.get_tools(ctx)
        assert len(result) == 1

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    async def test_unresolvable_tool_skipped(self, *_):
        skill = MagicMock()
        skill.tools = ["nonexistent_tool"]
        repo = _make_mock_repository({"s1": skill})
        ts = DynamicSkillToolSet(skill_repository=repo)
        ctx = _make_ctx(state_delta={loaded_state_key(_make_ctx(), "s1"): True})
        result = await ts.get_tools(ctx)
        assert len(result) == 0

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    async def test_no_tools_for_skill(self, *_):
        skill = MagicMock()
        skill.tools = []
        repo = _make_mock_repository({"s1": skill})
        ts = DynamicSkillToolSet(skill_repository=repo)
        ctx = _make_ctx(state_delta={loaded_state_key(_make_ctx(), "s1"): True})
        result = await ts.get_tools(ctx)
        assert result == []


# ---------------------------------------------------------------------------
# _find_tool_by_name / _find_tool_by_type
# ---------------------------------------------------------------------------

class TestFindToolMethods:
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    def test_find_by_name_not_found(self, *_):
        repo = _make_mock_repository()
        ts = DynamicSkillToolSet(skill_repository=repo)
        assert ts._find_tool_by_name("missing") is False

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    def test_find_by_type_unknown(self, *_):
        repo = _make_mock_repository()
        ts = DynamicSkillToolSet(skill_repository=repo)
        assert ts._find_tool_by_type("not_a_tool_object") is False


# ---------------------------------------------------------------------------
# _resolve_tool from toolsets
# ---------------------------------------------------------------------------

class TestResolveToolFromToolsets:
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    async def test_resolve_from_toolset(self, *_):
        repo = _make_mock_repository()
        ts = DynamicSkillToolSet(skill_repository=repo)
        mock_tool = _make_mock_tool("ts_tool")
        mock_toolset = _make_mock_toolset("my_ts", tools=[mock_tool])
        ts._available_toolsets = [mock_toolset]

        result = await ts._resolve_tool("ts_tool", _make_ctx())
        assert result is mock_tool

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    async def test_resolve_toolset_error(self, *_):
        repo = _make_mock_repository()
        ts = DynamicSkillToolSet(skill_repository=repo)
        bad_toolset = MagicMock()
        bad_toolset.name = "bad"
        bad_toolset.get_tools = AsyncMock(side_effect=RuntimeError("fail"))
        ts._available_toolsets = [bad_toolset]

        result = await ts._resolve_tool("missing", _make_ctx())
        assert result is None


# ---------------------------------------------------------------------------
# _get_tools_selection — bytes value
# ---------------------------------------------------------------------------

class TestGetToolsSelectionBytes:
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    def test_bytes_json_value(self, *_):
        skill = MagicMock()
        skill.tools = ["default"]
        repo = _make_mock_repository({"s1": skill})
        ts = DynamicSkillToolSet(skill_repository=repo)
        ctx = _make_ctx(state_delta={tool_state_key(_make_ctx(), "s1"): json.dumps(["t1"]).encode()})
        result = ts._get_tools_selection(ctx, "s1")
        assert result == ["t1"]

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    def test_bytes_star_value(self, *_):
        skill = MagicMock()
        skill.tools = ["default"]
        repo = _make_mock_repository({"s1": skill})
        ts = DynamicSkillToolSet(skill_repository=repo)
        ctx = _make_ctx(state_delta={tool_state_key(_make_ctx(), "s1"): b"*"})
        result = ts._get_tools_selection(ctx, "s1")
        assert result == ["default"]

    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool", return_value=None)
    @patch("trpc_agent_sdk.skills._dynamic_toolset.get_tool_set", return_value=None)
    def test_non_list_json_falls_back(self, *_):
        skill = MagicMock()
        skill.tools = ["default"]
        repo = _make_mock_repository({"s1": skill})
        ts = DynamicSkillToolSet(skill_repository=repo)
        ctx = _make_ctx(state_delta={tool_state_key(_make_ctx(), "s1"): json.dumps({"not": "list"})})
        result = ts._get_tools_selection(ctx, "s1")
        assert result == ["default"]
