# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Unit tests for trpc_agent_sdk.skills.tools._skill_list.

Covers:
- skill_list: returns skill names from repository
- skill_list: raises when repository not found
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.skills.tools._skill_list import skill_list


def _make_ctx(repository=None):
    ctx = MagicMock()
    ctx.agent_context.get_metadata = MagicMock(return_value=repository)
    return ctx


class TestSkillList:
    def test_returns_skill_names(self):
        repo = MagicMock()
        repo.skill_list = MagicMock(return_value=["skill-a", "skill-b"])
        ctx = _make_ctx(repository=repo)
        result = skill_list(ctx)
        assert result == ["skill-a", "skill-b"]

    def test_empty_repository(self):
        repo = MagicMock()
        repo.skill_list = MagicMock(return_value=[])
        ctx = _make_ctx(repository=repo)
        assert skill_list(ctx) == []

    def test_no_repository_raises(self):
        ctx = _make_ctx(repository=None)
        with pytest.raises(ValueError, match="repository not found"):
            skill_list(ctx)
