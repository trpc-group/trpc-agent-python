# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Unit tests for trpc_agent_sdk.skills.tools._skill_load.

Covers:
- _set_state_delta
- _set_state_delta_for_skill_load: docs and include_all_docs
- _set_state_delta_for_skill_tools
- skill_load: success, not found, with tools, with docs
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.skills._types import Skill, SkillSummary
from trpc_agent_sdk.skills.tools._skill_load import (
    _set_state_delta,
    _set_state_delta_for_skill_load,
    _set_state_delta_for_skill_tools,
    skill_load,
)


def _make_ctx(repository=None):
    ctx = MagicMock()
    ctx.actions.state_delta = {}
    ctx.agent_context.get_metadata = MagicMock(return_value=repository)
    return ctx


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
        _set_state_delta_for_skill_load(ctx, "test-skill", [])
        assert ctx.actions.state_delta["temp:skill:loaded:test-skill"] is True

    def test_sets_docs_as_json(self):
        ctx = MagicMock()
        ctx.actions.state_delta = {}
        _set_state_delta_for_skill_load(ctx, "test-skill", ["doc1.md", "doc2.md"])
        docs_value = ctx.actions.state_delta["temp:skill:docs:test-skill"]
        assert json.loads(docs_value) == ["doc1.md", "doc2.md"]

    def test_include_all_docs_sets_star(self):
        ctx = MagicMock()
        ctx.actions.state_delta = {}
        _set_state_delta_for_skill_load(ctx, "test-skill", [], include_all_docs=True)
        assert ctx.actions.state_delta["temp:skill:docs:test-skill"] == "*"


# ---------------------------------------------------------------------------
# _set_state_delta_for_skill_tools
# ---------------------------------------------------------------------------

class TestSetStateDeltaForSkillTools:
    def test_sets_tools_as_json(self):
        ctx = MagicMock()
        ctx.actions.state_delta = {}
        _set_state_delta_for_skill_tools(ctx, "test-skill", ["tool_a", "tool_b"])
        tools_value = ctx.actions.state_delta["temp:skill:tools:test-skill"]
        assert json.loads(tools_value) == ["tool_a", "tool_b"]

    def test_empty_tools(self):
        ctx = MagicMock()
        ctx.actions.state_delta = {}
        _set_state_delta_for_skill_tools(ctx, "test-skill", [])
        tools_value = ctx.actions.state_delta["temp:skill:tools:test-skill"]
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
        assert ctx.actions.state_delta["temp:skill:loaded:test"] is True

    def test_load_not_found(self):
        repo = MagicMock()
        repo.get = MagicMock(return_value=None)
        ctx = _make_ctx(repository=repo)

        result = skill_load(ctx, "nonexistent")
        assert "not found" in result

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
        tools_key = "temp:skill:tools:test"
        assert tools_key in ctx.actions.state_delta
        assert json.loads(ctx.actions.state_delta[tools_key]) == ["get_weather", "get_data"]

    def test_load_without_tools_does_not_set_tools_state(self):
        skill = Skill(summary=SkillSummary(name="test"), body="# Body")
        repo = MagicMock()
        repo.get = MagicMock(return_value=skill)
        ctx = _make_ctx(repository=repo)

        skill_load(ctx, "test")
        assert "temp:skill:tools:test" not in ctx.actions.state_delta

    def test_load_with_docs(self):
        skill = Skill(summary=SkillSummary(name="test"), body="# Body")
        repo = MagicMock()
        repo.get = MagicMock(return_value=skill)
        ctx = _make_ctx(repository=repo)

        skill_load(ctx, "test", docs=["doc1.md"])
        docs_key = "temp:skill:docs:test"
        assert json.loads(ctx.actions.state_delta[docs_key]) == ["doc1.md"]

    def test_load_include_all_docs(self):
        skill = Skill(summary=SkillSummary(name="test"), body="# Body")
        repo = MagicMock()
        repo.get = MagicMock(return_value=skill)
        ctx = _make_ctx(repository=repo)

        skill_load(ctx, "test", include_all_docs=True)
        docs_key = "temp:skill:docs:test"
        assert ctx.actions.state_delta[docs_key] == "*"
