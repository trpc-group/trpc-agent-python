# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Unit tests for trpc_agent_sdk.skills.stager._utils.

Covers:
- default_workspace_skill_dir
"""

from __future__ import annotations

from trpc_agent_sdk.skills.stager._utils import default_workspace_skill_dir


class TestDefaultWorkspaceSkillDir:
    def test_basic(self):
        result = default_workspace_skill_dir("weather")
        assert result == "skills/weather"

    def test_with_dashes(self):
        result = default_workspace_skill_dir("my-complex-skill")
        assert result == "skills/my-complex-skill"

    def test_empty_name(self):
        result = default_workspace_skill_dir("")
        assert result == "skills/"
