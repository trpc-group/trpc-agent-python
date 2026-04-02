# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Unit tests for trpc_agent_sdk.skills._toolset.

Covers:
- SkillToolSet initialization
- SkillToolSet.get_tools: returns expected tool set
- repository property
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trpc_agent_sdk.skills._toolset import SkillToolSet


def _make_ctx():
    ctx = MagicMock()
    ctx.agent_context = MagicMock()
    ctx.agent_context.with_metadata = MagicMock()
    return ctx


class TestSkillToolSetInit:
    def test_default_init(self, tmp_path):
        ts = SkillToolSet(paths=[str(tmp_path)])
        assert ts.name == "skill_toolset"
        assert ts.repository is not None

    def test_custom_repository(self):
        mock_repo = MagicMock()
        mock_repo.workspace_runtime = MagicMock()
        ts = SkillToolSet(repository=mock_repo)
        assert ts.repository is mock_repo


class TestSkillToolSetGetTools:
    async def test_get_tools_returns_tools(self, tmp_path):
        ts = SkillToolSet(paths=[str(tmp_path)])
        ctx = _make_ctx()
        tools = await ts.get_tools(ctx)
        assert len(tools) > 0

    async def test_get_tools_includes_run_and_exec(self, tmp_path):
        ts = SkillToolSet(paths=[str(tmp_path)])
        ctx = _make_ctx()
        tools = await ts.get_tools(ctx)
        tool_names = [t.name for t in tools]
        assert "skill_run" in tool_names
        assert "skill_exec" in tool_names

    async def test_get_tools_includes_function_tools(self, tmp_path):
        ts = SkillToolSet(paths=[str(tmp_path)])
        ctx = _make_ctx()
        tools = await ts.get_tools(ctx)
        tool_names = [t.name for t in tools]
        assert "skill_load" in tool_names
        assert "skill_list" in tool_names
        assert "skill_list_docs" in tool_names
        assert "skill_list_tools" in tool_names
        assert "skill_select_docs" in tool_names
        assert "skill_select_tools" in tool_names

    async def test_get_tools_sets_metadata(self, tmp_path):
        ts = SkillToolSet(paths=[str(tmp_path)])
        ctx = _make_ctx()
        await ts.get_tools(ctx)
        ctx.agent_context.with_metadata.assert_called()
