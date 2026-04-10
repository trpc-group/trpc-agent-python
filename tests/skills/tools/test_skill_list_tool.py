# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.skills.tools._skill_list_tool."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.skills._types import Skill, SkillSummary
from trpc_agent_sdk.skills.tools._skill_list_tool import (
    skill_list_tools,
)


# ---------------------------------------------------------------------------
# skill_list_tools
# ---------------------------------------------------------------------------

def _make_ctx(repository=None):
    ctx = MagicMock()
    ctx.agent_context.get_metadata = MagicMock(return_value=repository)
    return ctx


class TestSkillListTools:
    def test_returns_tools(self):
        skill = Skill(
            summary=SkillSummary(name="test"),
            body="Command:\n  python run.py\n\nOverview",
            tools=["get_weather", "get_data"],
        )
        repo = MagicMock()
        repo.get = MagicMock(return_value=skill)
        ctx = _make_ctx(repository=repo)

        result = skill_list_tools(ctx, "test")
        assert result["available_tools"] == ["get_weather", "get_data"]

    def test_skill_not_found(self):
        repo = MagicMock()
        repo.get = MagicMock(return_value=None)
        ctx = _make_ctx(repository=repo)

        result = skill_list_tools(ctx, "nonexistent")
        assert result == {"available_tools": []}

    def test_no_repository_raises(self):
        ctx = _make_ctx(repository=None)
        with pytest.raises(ValueError, match="repository not found"):
            skill_list_tools(ctx, "test")

    def test_no_tools_or_examples(self):
        skill = Skill(summary=SkillSummary(name="test"), body="# Overview\n")
        repo = MagicMock()
        repo.get = MagicMock(return_value=skill)
        ctx = _make_ctx(repository=repo)

        result = skill_list_tools(ctx, "test")
        assert result["available_tools"] == []
