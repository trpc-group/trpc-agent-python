# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.openclaw.skill._skill_tool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trpc_agent_sdk.server.openclaw.skill._skill_tool import (
    _SKILLS_DIRS,
    _create_workspace_runtime,
    _get_skill_paths,
    create_skill_tool_set,
)


# ---------------------------------------------------------------------------
# _get_skill_paths
# ---------------------------------------------------------------------------
class TestGetSkillPaths:

    def test_some_dirs_exist(self, tmp_path):
        repo = MagicMock()
        repo.workspace_skills_root = tmp_path

        (tmp_path / "local").mkdir()
        (tmp_path / "builtin").mkdir()
        # "local_file" and "network" do not exist

        result = _get_skill_paths(repo)

        assert tmp_path / "local" in result
        assert tmp_path / "builtin" in result
        assert len(result) == 2

    def test_all_dirs_exist(self, tmp_path):
        repo = MagicMock()
        repo.workspace_skills_root = tmp_path

        for d in _SKILLS_DIRS:
            (tmp_path / d).mkdir()

        result = _get_skill_paths(repo)
        assert len(result) == len(_SKILLS_DIRS)

    def test_no_dirs_exist(self, tmp_path):
        repo = MagicMock()
        repo.workspace_skills_root = tmp_path

        result = _get_skill_paths(repo)
        assert result == set()

    def test_returns_set_of_paths(self, tmp_path):
        repo = MagicMock()
        repo.workspace_skills_root = tmp_path
        (tmp_path / "local").mkdir()

        result = _get_skill_paths(repo)
        assert isinstance(result, set)
        for p in result:
            assert isinstance(p, Path)


# ---------------------------------------------------------------------------
# _create_workspace_runtime
# ---------------------------------------------------------------------------
class TestCreateWorkspaceRuntime:

    @patch("trpc_agent_sdk.server.openclaw.skill._skill_tool._create_local_workspace_runtime")
    def test_local_type(self, mock_create_local):
        mock_runtime = MagicMock()
        mock_create_local.return_value = mock_runtime

        config = MagicMock()
        config.skills.sandbox_type = "local"
        config.skills.local_config = MagicMock()
        repo = MagicMock()

        result = _create_workspace_runtime(config, repo)

        assert result is mock_runtime
        mock_create_local.assert_called_once_with(config.skills.local_config)

    @patch("trpc_agent_sdk.server.openclaw.skill._skill_tool._get_skill_paths")
    @patch("trpc_agent_sdk.server.openclaw.skill._skill_tool._create_container_workspace_runtime")
    def test_container_type(self, mock_create_container, mock_get_paths):
        mock_runtime = MagicMock()
        mock_create_container.return_value = mock_runtime
        mock_get_paths.return_value = {Path("/skills/local")}

        config = MagicMock()
        config.skills.sandbox_type = "container"
        config.skills.container_config = MagicMock()
        repo = MagicMock()
        repo.downloaded_skills_root = Path("/dl/skills")

        result = _create_workspace_runtime(config, repo)

        assert result is mock_runtime
        mock_create_container.assert_called_once_with(
            config.skills.container_config,
            {Path("/skills/local")},
            Path("/dl/skills"),
        )

    def test_invalid_type_raises(self):
        config = MagicMock()
        config.skills.sandbox_type = "unknown_type"
        repo = MagicMock()

        with pytest.raises(ValueError, match="Invalid workspace runtime type"):
            _create_workspace_runtime(config, repo)


# ---------------------------------------------------------------------------
# create_skill_tool_set
# ---------------------------------------------------------------------------
class TestCreateSkillToolSet:

    @patch("trpc_agent_sdk.server.openclaw.skill._skill_tool.SkillToolSet")
    @patch("trpc_agent_sdk.server.openclaw.skill._skill_tool._create_workspace_runtime")
    @patch("trpc_agent_sdk.server.openclaw.skill._skill_tool.ClawSkillLoader")
    def test_creates_tool_set_local(self, mock_loader_cls, mock_create_rt, mock_toolset_cls):
        mock_repo = MagicMock()
        mock_repo.downloaded_skills_root = Path("/dl")
        mock_loader_cls.return_value = mock_repo
        mock_runtime = MagicMock()
        mock_create_rt.return_value = mock_runtime
        mock_toolset = MagicMock()
        mock_toolset_cls.return_value = mock_toolset

        config = MagicMock()
        config.skills.sandbox_type = "local"
        config.skills.run_tool_kwargs = {"timeout": 30}

        result = create_skill_tool_set(config)

        mock_loader_cls.assert_called_once_with(config=config)
        mock_create_rt.assert_called_once_with(config=config, repository=mock_repo)
        mock_repo.set_workspace_runtime.assert_called_once_with(mock_runtime)
        mock_toolset_cls.assert_called_once()
        assert result is mock_toolset

    @patch("trpc_agent_sdk.server.openclaw.skill._skill_tool.SkillToolSet")
    @patch("trpc_agent_sdk.server.openclaw.skill._skill_tool._create_workspace_runtime")
    @patch("trpc_agent_sdk.server.openclaw.skill._skill_tool.ClawSkillLoader")
    def test_container_type_sets_env(self, mock_loader_cls, mock_create_rt, mock_toolset_cls):
        mock_repo = MagicMock()
        mock_repo.downloaded_skills_root = Path("/dl")
        mock_loader_cls.return_value = mock_repo
        mock_create_rt.return_value = MagicMock()
        mock_toolset_cls.return_value = MagicMock()

        config = MagicMock()
        config.skills.sandbox_type = "container"
        config.skills.run_tool_kwargs = {}

        result = create_skill_tool_set(config)

        call_kwargs = mock_toolset_cls.call_args[1]
        env = call_kwargs["run_tool_kwargs"]["env"]
        from trpc_agent_sdk.server.openclaw.config import TRPC_CLAW_SKILLS_INSTALL_ROOT_ENV_NAME
        assert TRPC_CLAW_SKILLS_INSTALL_ROOT_ENV_NAME in env

    @patch("trpc_agent_sdk.server.openclaw.skill._skill_tool.SkillToolSet")
    @patch("trpc_agent_sdk.server.openclaw.skill._skill_tool._create_workspace_runtime")
    @patch("trpc_agent_sdk.server.openclaw.skill._skill_tool.ClawSkillLoader")
    def test_local_type_sets_downloaded_root_in_env(self, mock_loader_cls, mock_create_rt, mock_toolset_cls):
        mock_repo = MagicMock()
        mock_repo.downloaded_skills_root = Path("/local/dl/skills")
        mock_loader_cls.return_value = mock_repo
        mock_create_rt.return_value = MagicMock()
        mock_toolset_cls.return_value = MagicMock()

        config = MagicMock()
        config.skills.sandbox_type = "local"
        config.skills.run_tool_kwargs = {}

        create_skill_tool_set(config)

        call_kwargs = mock_toolset_cls.call_args[1]
        env = call_kwargs["run_tool_kwargs"]["env"]
        from trpc_agent_sdk.server.openclaw.config import TRPC_CLAW_SKILLS_INSTALL_ROOT_ENV_NAME
        assert env[TRPC_CLAW_SKILLS_INSTALL_ROOT_ENV_NAME] == str(Path("/local/dl/skills"))

    @patch("trpc_agent_sdk.server.openclaw.skill._skill_tool.SkillToolSet")
    @patch("trpc_agent_sdk.server.openclaw.skill._skill_tool._create_workspace_runtime")
    @patch("trpc_agent_sdk.server.openclaw.skill._skill_tool.ClawSkillLoader")
    def test_none_run_tool_kwargs(self, mock_loader_cls, mock_create_rt, mock_toolset_cls):
        mock_repo = MagicMock()
        mock_repo.downloaded_skills_root = Path("/dl")
        mock_loader_cls.return_value = mock_repo
        mock_create_rt.return_value = MagicMock()
        mock_toolset_cls.return_value = MagicMock()

        config = MagicMock()
        config.skills.sandbox_type = "local"
        config.skills.run_tool_kwargs = None

        create_skill_tool_set(config)

        mock_toolset_cls.assert_called_once()

    @patch("trpc_agent_sdk.server.openclaw.skill._skill_tool.SkillToolSet")
    @patch("trpc_agent_sdk.server.openclaw.skill._skill_tool._create_workspace_runtime")
    @patch("trpc_agent_sdk.server.openclaw.skill._skill_tool.ClawSkillLoader")
    def test_preserves_existing_env(self, mock_loader_cls, mock_create_rt, mock_toolset_cls):
        mock_repo = MagicMock()
        mock_repo.downloaded_skills_root = Path("/dl")
        mock_loader_cls.return_value = mock_repo
        mock_create_rt.return_value = MagicMock()
        mock_toolset_cls.return_value = MagicMock()

        config = MagicMock()
        config.skills.sandbox_type = "local"
        config.skills.run_tool_kwargs = {"env": {"MY_VAR": "keep"}}

        create_skill_tool_set(config)

        call_kwargs = mock_toolset_cls.call_args[1]
        env = call_kwargs["run_tool_kwargs"]["env"]
        assert env["MY_VAR"] == "keep"
