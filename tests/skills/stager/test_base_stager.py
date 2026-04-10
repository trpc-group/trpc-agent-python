# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.skills.stager._base_stager.

Covers:
- Stager.stage_skill: fresh staging, cached (digest match), re-staging
- Stager.load_workspace_metadata / save_workspace_metadata
- Stager.skill_links_present
- Stager.remove_workspace_path
- Stager.create_stager factory
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from trpc_agent_sdk.skills.stager._base_stager import Stager
from trpc_agent_sdk.skills._types import SkillWorkspaceMetadata, SkillMetadata


def _make_ctx():
    ctx = MagicMock()
    ctx.actions.state_delta = {}
    ctx.session_state = {}
    return ctx


def _make_workspace(path="/tmp/ws"):
    ws = MagicMock()
    ws.path = path
    return ws


def _make_runtime(fs_collect_return=None, runner_exit_code=0):
    runtime = MagicMock()
    fs = MagicMock()
    runner = MagicMock()

    if fs_collect_return is not None:
        fs.collect = AsyncMock(return_value=fs_collect_return)
    else:
        fs.collect = AsyncMock(return_value=[])
    fs.stage_directory = AsyncMock()
    fs.put_files = AsyncMock()

    run_result = MagicMock()
    run_result.exit_code = runner_exit_code
    run_result.stderr = ""
    runner.run_program = AsyncMock(return_value=run_result)

    runtime.fs = MagicMock(return_value=fs)
    runtime.runner = MagicMock(return_value=runner)
    return runtime


def _make_repository(path="/skills/test-skill"):
    repo = MagicMock()
    repo.path = MagicMock(return_value=path)
    repo.workspace_runtime = _make_runtime()
    return repo


def _make_request(skill_name="test-skill", repo=None, ws=None, ctx=None):
    from trpc_agent_sdk.skills.stager._types import SkillStageRequest
    return SkillStageRequest(
        skill_name=skill_name,
        repository=repo or _make_repository(),
        workspace=ws or _make_workspace(),
        ctx=ctx or _make_ctx(),
    )


# ---------------------------------------------------------------------------
# create_stager
# ---------------------------------------------------------------------------

class TestCreateStager:
    def test_creates_instance(self):
        stager = Stager.create_stager()
        assert isinstance(stager, Stager)


# ---------------------------------------------------------------------------
# load_workspace_metadata
# ---------------------------------------------------------------------------

class TestLoadWorkspaceMetadata:
    async def test_empty_file_returns_default(self):
        stager = Stager()
        ctx = _make_ctx()
        runtime = _make_runtime(fs_collect_return=[])
        ws = _make_workspace()
        md = await stager.load_workspace_metadata(ctx, runtime, ws)
        assert isinstance(md, SkillWorkspaceMetadata)
        assert md.version == 1

    async def test_valid_json_parsed(self):
        stager = Stager()
        ctx = _make_ctx()
        ws = _make_workspace()

        mock_file = MagicMock()
        md_data = {
            "version": 2,
            "skills": {"s1": {"name": "s1", "digest": "d1", "mounted": True}},
        }
        mock_file.content = json.dumps(md_data)
        runtime = _make_runtime(fs_collect_return=[mock_file])

        md = await stager.load_workspace_metadata(ctx, runtime, ws)
        assert md.version == 2
        assert "s1" in md.skills

    async def test_invalid_json_returns_default(self):
        stager = Stager()
        ctx = _make_ctx()
        ws = _make_workspace()

        mock_file = MagicMock()
        mock_file.content = "not json"
        runtime = _make_runtime(fs_collect_return=[mock_file])

        md = await stager.load_workspace_metadata(ctx, runtime, ws)
        assert md.version == 1

    async def test_empty_content_returns_default(self):
        stager = Stager()
        ctx = _make_ctx()
        ws = _make_workspace()

        mock_file = MagicMock()
        mock_file.content = "   "
        runtime = _make_runtime(fs_collect_return=[mock_file])

        md = await stager.load_workspace_metadata(ctx, runtime, ws)
        assert md.version == 1


# ---------------------------------------------------------------------------
# save_workspace_metadata
# ---------------------------------------------------------------------------

class TestSaveWorkspaceMetadata:
    async def test_saves_metadata(self):
        stager = Stager()
        ctx = _make_ctx()
        runtime = _make_runtime()
        ws = _make_workspace()
        md = SkillWorkspaceMetadata(version=3)

        await stager.save_workspace_metadata(ctx, runtime, ws, md)

        runtime.fs(ctx).put_files.assert_called_once()
        runtime.runner(ctx).run_program.assert_called_once()

    async def test_sets_version_if_missing(self):
        stager = Stager()
        ctx = _make_ctx()
        runtime = _make_runtime()
        ws = _make_workspace()
        md = SkillWorkspaceMetadata()
        md.version = 0

        await stager.save_workspace_metadata(ctx, runtime, ws, md)
        assert md.version == 1

    async def test_sets_timestamps(self):
        stager = Stager()
        ctx = _make_ctx()
        runtime = _make_runtime()
        ws = _make_workspace()
        md = SkillWorkspaceMetadata(version=1)

        await stager.save_workspace_metadata(ctx, runtime, ws, md)
        assert md.updated_at is not None
        assert md.last_access is not None
        assert md.created_at is not None


# ---------------------------------------------------------------------------
# skill_links_present
# ---------------------------------------------------------------------------

class TestSkillLinksPresent:
    async def test_links_present(self):
        stager = Stager()
        ctx = _make_ctx()
        runtime = _make_runtime(runner_exit_code=0)
        ws = _make_workspace()
        result = await stager.skill_links_present(ctx, runtime, ws, "test")
        assert result is True

    async def test_links_missing(self):
        stager = Stager()
        ctx = _make_ctx()
        runtime = _make_runtime(runner_exit_code=1)
        ws = _make_workspace()
        result = await stager.skill_links_present(ctx, runtime, ws, "test")
        assert result is False

    async def test_empty_name_returns_false(self):
        stager = Stager()
        ctx = _make_ctx()
        runtime = _make_runtime()
        ws = _make_workspace()
        result = await stager.skill_links_present(ctx, runtime, ws, "")
        assert result is False

    async def test_whitespace_name_returns_false(self):
        stager = Stager()
        ctx = _make_ctx()
        runtime = _make_runtime()
        ws = _make_workspace()
        result = await stager.skill_links_present(ctx, runtime, ws, "   ")
        assert result is False


# ---------------------------------------------------------------------------
# remove_workspace_path
# ---------------------------------------------------------------------------

class TestRemoveWorkspacePath:
    async def test_removes_path(self):
        stager = Stager()
        ctx = _make_ctx()
        runtime = _make_runtime()
        ws = _make_workspace()
        await stager.remove_workspace_path(ctx, runtime, ws, "skills/test")
        runtime.runner(ctx).run_program.assert_called_once()

    async def test_empty_path_is_noop(self):
        stager = Stager()
        ctx = _make_ctx()
        runtime = _make_runtime()
        ws = _make_workspace()
        await stager.remove_workspace_path(ctx, runtime, ws, "")
        runtime.runner(ctx).run_program.assert_not_called()

    async def test_whitespace_path_is_noop(self):
        stager = Stager()
        ctx = _make_ctx()
        runtime = _make_runtime()
        ws = _make_workspace()
        await stager.remove_workspace_path(ctx, runtime, ws, "   ")
        runtime.runner(ctx).run_program.assert_not_called()


# ---------------------------------------------------------------------------
# stage_skill
# ---------------------------------------------------------------------------

class TestStageSkill:
    @patch("trpc_agent_sdk.skills.stager._base_stager.compute_dir_digest", return_value="new_digest")
    async def test_fresh_staging(self, mock_digest):
        stager = Stager()
        request = _make_request()
        runtime = request.repository.workspace_runtime

        mock_file = MagicMock()
        mock_file.content = json.dumps({"version": 1, "skills": {}})
        runtime.fs(request.ctx).collect = AsyncMock(return_value=[mock_file])

        result = await stager.stage_skill(request)
        assert result.workspace_skill_dir == "skills/test-skill"

    @patch("trpc_agent_sdk.skills.stager._base_stager.compute_dir_digest", return_value="same_digest")
    async def test_cached_staging_with_links(self, mock_digest):
        stager = Stager()
        request = _make_request()
        runtime = request.repository.workspace_runtime

        md_data = {
            "version": 1,
            "skills": {
                "test-skill": {
                    "name": "test-skill",
                    "digest": "same_digest",
                    "mounted": True,
                },
            },
        }
        mock_file = MagicMock()
        mock_file.content = json.dumps(md_data)
        runtime.fs(request.ctx).collect = AsyncMock(return_value=[mock_file])

        # Make links present
        run_result = MagicMock()
        run_result.exit_code = 0
        run_result.stderr = ""
        runtime.runner(request.ctx).run_program = AsyncMock(return_value=run_result)

        result = await stager.stage_skill(request)
        assert result.workspace_skill_dir == "skills/test-skill"
