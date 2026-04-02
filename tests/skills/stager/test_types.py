# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Unit tests for trpc_agent_sdk.skills.stager._types.

Covers:
- SkillStageRequest dataclass fields
- SkillStageResult dataclass fields
"""

from __future__ import annotations

from unittest.mock import MagicMock

from trpc_agent_sdk.skills.stager._types import SkillStageRequest, SkillStageResult


class TestSkillStageRequest:
    def test_creation(self):
        req = SkillStageRequest(
            skill_name="test",
            repository=MagicMock(),
            workspace=MagicMock(),
            ctx=MagicMock(),
        )
        assert req.skill_name == "test"
        assert req.engine is None
        assert req.timeout == 300.0

    def test_custom_timeout(self):
        req = SkillStageRequest(
            skill_name="test",
            repository=MagicMock(),
            workspace=MagicMock(),
            ctx=MagicMock(),
            timeout=60.0,
        )
        assert req.timeout == 60.0


class TestSkillStageResult:
    def test_creation(self):
        result = SkillStageResult(workspace_skill_dir="skills/test")
        assert result.workspace_skill_dir == "skills/test"
