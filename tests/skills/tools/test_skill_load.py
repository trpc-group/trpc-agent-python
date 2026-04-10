# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.skills.tools._skill_load.

Covers:
- _set_state_delta
- _set_state_delta_for_skill_load: docs and include_all_docs
- _set_state_delta_for_skill_tools
- skill_load: success, not found, with tools, with docs
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from trpc_agent_sdk.skills._common import docs_state_key
from trpc_agent_sdk.skills._common import loaded_state_key
from trpc_agent_sdk.skills._common import set_state_delta
from trpc_agent_sdk.skills._common import tool_state_key
from trpc_agent_sdk.skills._constants import SKILL_REPOSITORY_KEY
from trpc_agent_sdk.skills._types import Skill, SkillSummary
from trpc_agent_sdk.skills.tools._skill_load import (
    SkillLoadTool,
)


def _make_ctx(repository=None):
    ctx = MagicMock()
    ctx.actions.state_delta = {}
    ctx.agent_context.get_metadata = MagicMock(return_value=repository)
    ctx.agent_name = ""
    return ctx


def _set_state_delta_for_skill_load(ctx, skill_name: str, docs: list[str], include_all_docs: bool = False):
    set_state_delta(ctx, loaded_state_key(ctx, skill_name), True)
    set_state_delta(
        ctx,
        docs_state_key(ctx, skill_name),
        "*" if include_all_docs else json.dumps(docs or []),
    )


def _set_state_delta_for_skill_tools(ctx, skill_name: str, tools: list[str]):
    set_state_delta(ctx, tool_state_key(ctx, skill_name), json.dumps(tools or []))


def _set_state_delta(ctx, key: str, value: str):
    set_state_delta(ctx, key, value)


def skill_load(ctx, skill_name: str, docs: list[str] | None = None, include_all_docs: bool = False) -> str:
    repository = ctx.agent_context.get_metadata(SKILL_REPOSITORY_KEY)
    if repository is None:
        raise ValueError("repository not found")
    tool = SkillLoadTool(repository=repository)
    with patch.object(SkillLoadTool, "_ensure_staged", new=AsyncMock(return_value=None)):
        return asyncio.run(
            tool._run_async_impl(
                tool_context=ctx,
                args={
                    "skill_name": skill_name,
                    "docs": docs or [],
                    "include_all_docs": include_all_docs,
                },
            ))


# ---------------------------------------------------------------------------
# _set_state_delta
# ---------------------------------------------------------------------------

class TestSetStateDelta:
    def test_sets_value(self):
        ctx = MagicMock()
        ctx.actions.state_delta = {}
        _set_state_delta(ctx, "key", "value")
        assert ctx.actions.state_delta["key"] == "value"


# ---------------------------------------------------------------------------
# _set_state_delta_for_skill_load
# ---------------------------------------------------------------------------

class TestSetStateDeltaForSkillLoad:
    def test_sets_loaded_flag(self):
        ctx = MagicMock()
        ctx.actions.state_delta = {}
        ctx.agent_name = ""
        _set_state_delta_for_skill_load(ctx, "test-skill", [])
        assert ctx.actions.state_delta[loaded_state_key(ctx, "test-skill")] is True

    def test_sets_docs_as_json(self):
        ctx = MagicMock()
        ctx.actions.state_delta = {}
        ctx.agent_name = ""
        _set_state_delta_for_skill_load(ctx, "test-skill", ["doc1.md", "doc2.md"])
        docs_value = ctx.actions.state_delta[docs_state_key(ctx, "test-skill")]
        assert json.loads(docs_value) == ["doc1.md", "doc2.md"]

    def test_include_all_docs_sets_star(self):
        ctx = MagicMock()
        ctx.actions.state_delta = {}
        ctx.agent_name = ""
        _set_state_delta_for_skill_load(ctx, "test-skill", [], include_all_docs=True)
        assert ctx.actions.state_delta[docs_state_key(ctx, "test-skill")] == "*"


# ---------------------------------------------------------------------------
# _set_state_delta_for_skill_tools
# ---------------------------------------------------------------------------

class TestSetStateDeltaForSkillTools:
    def test_sets_tools_as_json(self):
        ctx = MagicMock()
        ctx.actions.state_delta = {}
        ctx.agent_name = ""
        _set_state_delta_for_skill_tools(ctx, "test-skill", ["tool_a", "tool_b"])
        tools_value = ctx.actions.state_delta[tool_state_key(ctx, "test-skill")]
        assert json.loads(tools_value) == ["tool_a", "tool_b"]

    def test_empty_tools(self):
        ctx = MagicMock()
        ctx.actions.state_delta = {}
        ctx.agent_name = ""
        _set_state_delta_for_skill_tools(ctx, "test-skill", [])
        tools_value = ctx.actions.state_delta[tool_state_key(ctx, "test-skill")]
        assert json.loads(tools_value) == []


# ---------------------------------------------------------------------------
# skill_load
# ---------------------------------------------------------------------------

class TestSkillLoad:
    def test_load_success(self):
        skill = Skill(summary=SkillSummary(name="test"), body="# Test Body")
        repo = MagicMock()
        repo.get = MagicMock(return_value=skill)
        ctx = _make_ctx(repository=repo)

        result = skill_load(ctx, "test")
        assert "loaded" in result
        assert ctx.actions.state_delta[loaded_state_key(ctx, "test")] is True

    def test_load_not_found(self):
        repo = MagicMock()
        repo.get = MagicMock(side_effect=ValueError("not found"))
        ctx = _make_ctx(repository=repo)

        with pytest.raises(ValueError, match="not found"):
            skill_load(ctx, "nonexistent")

    def test_load_no_repository_raises(self):
        ctx = _make_ctx(repository=None)
        with pytest.raises(ValueError, match="repository not found"):
            skill_load(ctx, "test")

    def test_load_with_tools_sets_tools_state(self):
        skill = Skill(
            summary=SkillSummary(name="test"),
            body="# Body",
            tools=["get_weather", "get_data"],
        )
        repo = MagicMock()
        repo.get = MagicMock(return_value=skill)
        ctx = _make_ctx(repository=repo)

        skill_load(ctx, "test")
        tools_key = tool_state_key(ctx, "test")
        assert tools_key in ctx.actions.state_delta
        assert json.loads(ctx.actions.state_delta[tools_key]) == ["get_weather", "get_data"]

    def test_load_without_tools_does_not_set_tools_state(self):
        skill = Skill(summary=SkillSummary(name="test"), body="# Body")
        repo = MagicMock()
        repo.get = MagicMock(return_value=skill)
        ctx = _make_ctx(repository=repo)

        skill_load(ctx, "test")
        assert tool_state_key(ctx, "test") not in ctx.actions.state_delta

    def test_load_with_docs(self):
        skill = Skill(summary=SkillSummary(name="test"), body="# Body")
        repo = MagicMock()
        repo.get = MagicMock(return_value=skill)
        ctx = _make_ctx(repository=repo)

        skill_load(ctx, "test", docs=["doc1.md"])
        docs_key = docs_state_key(ctx, "test")
        assert json.loads(ctx.actions.state_delta[docs_key]) == ["doc1.md"]

    def test_load_include_all_docs(self):
        skill = Skill(summary=SkillSummary(name="test"), body="# Body")
        repo = MagicMock()
        repo.get = MagicMock(return_value=skill)
        ctx = _make_ctx(repository=repo)

        skill_load(ctx, "test", include_all_docs=True)
        docs_key = docs_state_key(ctx, "test")
        assert ctx.actions.state_delta[docs_key] == "*"
