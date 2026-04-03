# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.skills.tools._skill_select_docs.

Covers:
- SkillSelectDocsResult: alias field mapping
- skill_select_docs: replace, add, clear modes
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.skills.tools._skill_select_docs import (
    SkillSelectDocsResult,
    skill_select_docs,
)


def _make_ctx(state_delta=None, session_state=None):
    ctx = MagicMock()
    ctx.actions.state_delta = state_delta or {}
    ctx.session_state = session_state or {}
    return ctx


# ---------------------------------------------------------------------------
# SkillSelectDocsResult
# ---------------------------------------------------------------------------

class TestSkillSelectDocsResult:
    def test_alias_selected_items(self):
        result = SkillSelectDocsResult(
            skill="test",
            selected_items=["doc1.md", "doc2.md"],
            include_all=False,
        )
        assert result.selected_docs == ["doc1.md", "doc2.md"]
        assert result.include_all_docs is False

    def test_alias_include_all(self):
        result = SkillSelectDocsResult(
            skill="test",
            selected_items=[],
            include_all=True,
        )
        assert result.include_all_docs is True
        assert result.selected_docs == []

    def test_direct_field_setting(self):
        result = SkillSelectDocsResult(
            skill="test",
            selected_docs=["a.md"],
            include_all_docs=True,
        )
        assert result.selected_docs == ["a.md"]
        assert result.include_all_docs is True


# ---------------------------------------------------------------------------
# skill_select_docs
# ---------------------------------------------------------------------------

class TestSkillSelectDocs:
    def test_replace_mode(self):
        ctx = _make_ctx()
        result = skill_select_docs(ctx, "test-skill", docs=["doc1.md", "doc2.md"], mode="replace")
        assert result.skill == "test-skill"
        assert result.mode == "replace"
        assert result.selected_docs == ["doc1.md", "doc2.md"]

    def test_add_mode(self):
        ctx = _make_ctx(session_state={
            "temp:skill:docs:test-skill": json.dumps(["existing.md"]),
        })
        result = skill_select_docs(ctx, "test-skill", docs=["new.md"], mode="add")
        assert result.mode == "add"
        assert "existing.md" in result.selected_docs
        assert "new.md" in result.selected_docs

    def test_clear_mode(self):
        ctx = _make_ctx(session_state={
            "temp:skill:docs:test-skill": json.dumps(["some.md"]),
        })
        result = skill_select_docs(ctx, "test-skill", mode="clear")
        assert result.mode == "clear"
        assert result.selected_docs == []

    def test_include_all_docs(self):
        ctx = _make_ctx()
        result = skill_select_docs(ctx, "test-skill", include_all_docs=True, mode="replace")
        assert result.include_all_docs is True

    def test_updates_state_delta(self):
        ctx = _make_ctx()
        skill_select_docs(ctx, "test-skill", docs=["a.md"], mode="replace")
        key = "temp:skill:docs:test-skill"
        assert key in ctx.actions.state_delta
