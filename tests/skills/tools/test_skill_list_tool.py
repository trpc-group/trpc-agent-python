# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.skills.tools._skill_list_tool.

Covers:
- _extract_shell_examples_from_skill_body: Command section parsing
- skill_list_tools: returns tools and command examples
- skill_list_tools: handles missing skill / repository
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.skills._types import Skill, SkillSummary
from trpc_agent_sdk.skills.tools._skill_list_tool import (
    _extract_shell_examples_from_skill_body,
    skill_list_tools,
)


# ---------------------------------------------------------------------------
# _extract_shell_examples_from_skill_body
# ---------------------------------------------------------------------------

class TestExtractShellExamples:
    def test_empty_body(self):
        assert _extract_shell_examples_from_skill_body("") == []

    def test_command_section(self):
        body = "Command:\n  python scripts/run.py --input data.csv\n\nOverview"
        result = _extract_shell_examples_from_skill_body(body)
        assert len(result) >= 1
        assert "python scripts/run.py" in result[0]

    def test_limit(self):
        body = ""
        for i in range(10):
            body += f"Command:\n  cmd_{i} --arg\n\n"
        result = _extract_shell_examples_from_skill_body(body, limit=3)
        assert len(result) <= 3

    def test_stops_at_section_break(self):
        body = "Command:\n  python run.py\n\nOutput files\nMore content"
        result = _extract_shell_examples_from_skill_body(body)
        assert len(result) == 1

    def test_multiline_command(self):
        body = "Command:\n  python scripts/long.py \\\n    --arg1 val1 \\\n    --arg2 val2\n\n"
        result = _extract_shell_examples_from_skill_body(body)
        assert len(result) >= 1

    def test_no_command_section(self):
        body = "# Overview\nJust a description.\n"
        result = _extract_shell_examples_from_skill_body(body)
        assert result == []

    def test_deduplication(self):
        body = "Command:\n  python run.py\n\nCommand:\n  python run.py\n"
        result = _extract_shell_examples_from_skill_body(body)
        assert len(result) == 1

    def test_rejects_non_command_starting_chars(self):
        body = "Command:\n  !not_a_command\n\n"
        result = _extract_shell_examples_from_skill_body(body)
        assert result == []

    def test_command_with_numbered_break(self):
        body = "Command:\n  python run.py\n\n1) Next section\n"
        result = _extract_shell_examples_from_skill_body(body)
        assert len(result) == 1

    def test_skips_empty_lines_before_command(self):
        body = "Command:\n\n\n  python run.py\n\nEnd"
        result = _extract_shell_examples_from_skill_body(body)
        assert len(result) >= 1

    def test_stops_at_tools_section(self):
        body = "Command:\n  python run.py\n\ntools:\n- tool1"
        result = _extract_shell_examples_from_skill_body(body)
        assert len(result) == 1

    def test_whitespace_normalization(self):
        body = "Command:\n  python   run.py    --arg   val\n\n"
        result = _extract_shell_examples_from_skill_body(body)
        assert len(result) == 1
        assert "  " not in result[0]


# ---------------------------------------------------------------------------
# skill_list_tools
# ---------------------------------------------------------------------------

def _make_ctx(repository=None):
    ctx = MagicMock()
    ctx.agent_context.get_metadata = MagicMock(return_value=repository)
    return ctx


class TestSkillListTools:
    def test_returns_tools_and_examples(self):
        skill = Skill(
            summary=SkillSummary(name="test"),
            body="Command:\n  python run.py\n\nOverview",
            tools=["get_weather", "get_data"],
        )
        repo = MagicMock()
        repo.get = MagicMock(return_value=skill)
        ctx = _make_ctx(repository=repo)

        result = skill_list_tools(ctx, "test")
        assert result["tools"] == ["get_weather", "get_data"]
        assert len(result["command_examples"]) >= 1

    def test_skill_not_found(self):
        repo = MagicMock()
        repo.get = MagicMock(return_value=None)
        ctx = _make_ctx(repository=repo)

        result = skill_list_tools(ctx, "nonexistent")
        assert result == {"tools": [], "command_examples": []}

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
        assert result["tools"] == []
