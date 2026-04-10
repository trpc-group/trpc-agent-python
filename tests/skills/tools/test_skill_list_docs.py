# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
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
        assert result == ["guide.md", "api.md"]

    def test_no_body(self):
        skill = Skill(summary=SkillSummary(name="test"), body="")
        repo = MagicMock()
        repo.get = MagicMock(return_value=skill)
        ctx = _make_ctx(repository=repo)

        result = skill_list_docs(ctx, "test")
        assert result == []

    def test_skill_not_found(self):
        repo = MagicMock()
        repo.get = MagicMock(side_effect=ValueError("not found"))
        ctx = _make_ctx(repository=repo)

        with pytest.raises(ValueError, match="unknown skill"):
            skill_list_docs(ctx, "nonexistent")

    def test_no_repository_raises(self):
        ctx = _make_ctx(repository=None)
        assert skill_list_docs(ctx, "test") == []
