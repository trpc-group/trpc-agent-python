# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Unit tests for trpc_agent_sdk.skills.tools._copy_stager.

Covers:
- normalize_workspace_skill_dir: valid paths, edge cases, rejected paths
- _normalize_skill_stage_result
- CopySkillStager.stage_skill: validation, delegation
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trpc_agent_sdk.skills.tools._copy_stager import (
    CopySkillStager,
    normalize_workspace_skill_dir,
    _normalize_skill_stage_result,
)
from trpc_agent_sdk.skills.stager._types import SkillStageResult


# ---------------------------------------------------------------------------
# normalize_workspace_skill_dir
# ---------------------------------------------------------------------------

class TestNormalizeWorkspaceSkillDir:
    def test_valid_skills_path(self):
        assert normalize_workspace_skill_dir("skills/weather") == "skills/weather"

    def test_valid_work_path(self):
        assert normalize_workspace_skill_dir("work/data") == "work/data"

    def test_valid_out_path(self):
        assert normalize_workspace_skill_dir("out/results") == "out/results"

    def test_valid_runs_path(self):
        assert normalize_workspace_skill_dir("runs/run1") == "runs/run1"

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            normalize_workspace_skill_dir("")

    def test_whitespace_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            normalize_workspace_skill_dir("   ")

    def test_escaping_workspace_raises(self):
        with pytest.raises(ValueError, match="must stay within"):
            normalize_workspace_skill_dir("etc/passwd")

    def test_leading_slash_normalized(self):
        result = normalize_workspace_skill_dir("/skills/test")
        assert result == "skills/test"

    def test_backslash_normalized(self):
        result = normalize_workspace_skill_dir("skills\\test")
        assert result == "skills/test"

    def test_dot_path(self):
        result = normalize_workspace_skill_dir("/")
        assert result == "."

    def test_parent_path_raises(self):
        with pytest.raises(ValueError, match="must stay within"):
            normalize_workspace_skill_dir("../escape")


# ---------------------------------------------------------------------------
# _normalize_skill_stage_result
# ---------------------------------------------------------------------------

class TestNormalizeSkillStageResult:
    def test_normalizes(self):
        result = SkillStageResult(workspace_skill_dir="skills/test")
        normalized = _normalize_skill_stage_result(result)
        assert normalized.workspace_skill_dir == "skills/test"

    def test_invalid_raises(self):
        result = SkillStageResult(workspace_skill_dir="")
        with pytest.raises(ValueError):
            _normalize_skill_stage_result(result)


# ---------------------------------------------------------------------------
# CopySkillStager
# ---------------------------------------------------------------------------

class TestCopySkillStager:
    async def test_no_repository_raises(self):
        stager = CopySkillStager()
        from trpc_agent_sdk.skills.stager._types import SkillStageRequest
        request = SkillStageRequest(
            skill_name="test",
            repository=None,
            workspace=MagicMock(),
            ctx=MagicMock(),
        )
        with pytest.raises(ValueError, match="repository"):
            await stager.stage_skill(request)

    @patch("trpc_agent_sdk.skills.stager._base_stager.compute_dir_digest", return_value="digest")
    async def test_stage_delegates_to_parent(self, mock_digest):
        stager = CopySkillStager()
        repo = MagicMock()
        repo.path = MagicMock(return_value="/skills/test")
        runtime = MagicMock()
        fs = MagicMock()
        runner = MagicMock()

        mock_file = MagicMock()
        mock_file.content = json.dumps({"version": 1, "skills": {}})
        fs.collect = AsyncMock(return_value=[mock_file])
        fs.stage_directory = AsyncMock()
        fs.put_files = AsyncMock()

        run_result = MagicMock()
        run_result.exit_code = 0
        run_result.stderr = ""
        runner.run_program = AsyncMock(return_value=run_result)

        runtime.fs = MagicMock(return_value=fs)
        runtime.runner = MagicMock(return_value=runner)
        repo.workspace_runtime = runtime

        from trpc_agent_sdk.skills.stager._types import SkillStageRequest
        request = SkillStageRequest(
            skill_name="test",
            repository=repo,
            workspace=MagicMock(path="/tmp/ws"),
            ctx=MagicMock(),
        )

        result = await stager.stage_skill(request)
        assert result.workspace_skill_dir == "skills/test"
