# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Unit tests for trpc_agent_sdk.skills.tools._skill_list_docs.

Covers:
- skill_list_docs: returns docs and body_loaded status
- skill_list_docs: handles missing skill
- skill_list_docs: raises when no repository
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.skills._types import Skill, SkillResource, SkillSummary
from trpc_agent_sdk.skills.tools._skill_list_docs import skill_list_docs


def _make_ctx(repository=None):
    ctx = MagicMock()
    ctx.agent_context.get_metadata = MagicMock(return_value=repository)
    return ctx


class TestSkillListDocs:
    def test_returns_docs(self):
        skill = Skill(
            summary=SkillSummary(name="test"),
            body="# Body",
            resources=[
                SkillResource(path="guide.md", content="guide"),
                SkillResource(path="api.md", content="api"),
            ],
        )
        repo = MagicMock()
        repo.get = MagicMock(return_value=skill)
        ctx = _make_ctx(repository=repo)

        result = skill_list_docs(ctx, "test")
        assert result["docs"] == ["guide.md", "api.md"]
        assert result["body_loaded"] is True

    def test_no_body(self):
        skill = Skill(summary=SkillSummary(name="test"), body="")
        repo = MagicMock()
        repo.get = MagicMock(return_value=skill)
        ctx = _make_ctx(repository=repo)

        result = skill_list_docs(ctx, "test")
        assert result["body_loaded"] is False

    def test_skill_not_found(self):
        repo = MagicMock()
        repo.get = MagicMock(return_value=None)
        ctx = _make_ctx(repository=repo)

        result = skill_list_docs(ctx, "nonexistent")
        assert result == {"docs": [], "body_loaded": False}

    def test_no_repository_raises(self):
        ctx = _make_ctx(repository=None)
        with pytest.raises(ValueError, match="repository not found"):
            skill_list_docs(ctx, "test")
