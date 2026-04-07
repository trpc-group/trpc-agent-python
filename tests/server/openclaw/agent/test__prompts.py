"""Unit tests for trpc_agent_sdk.server.openclaw.agent._prompts."""

from __future__ import annotations

import platform
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trpc_agent_sdk.server.openclaw.agent._prompts import (
    INSTRUCTION_DEFAULT,
    SYSTEM_PROMPT_DEFAULT,
    ClawPrompts,
)
from trpc_agent_sdk.server.openclaw.config import BOT_NAME


def _make_config(
    workspace=None,
    instruction="",
    system_prompt="",
    personal=None,
):
    config = MagicMock()
    ws = workspace or Path("/tmp/test_workspace")
    config.workspace = ws
    config.agent = MagicMock()
    config.agent.instruction = instruction
    config.agent.system_prompt = system_prompt
    config.personal = personal or []
    return config


class TestGetIdentity:

    def test_default_instruction_and_system_prompt(self, tmp_path):
        config = _make_config(workspace=tmp_path)
        prompts = ClawPrompts(config=config)

        identity = prompts._get_identity()

        assert BOT_NAME in identity
        assert INSTRUCTION_DEFAULT in identity
        assert SYSTEM_PROMPT_DEFAULT in identity
        assert "Runtime" in identity
        assert "Workspace" in identity

    def test_custom_instruction(self, tmp_path):
        config = _make_config(workspace=tmp_path, instruction="Custom instruction here")
        prompts = ClawPrompts(config=config)

        identity = prompts._get_identity()

        assert "Custom instruction here" in identity
        assert INSTRUCTION_DEFAULT not in identity

    def test_custom_system_prompt(self, tmp_path):
        config = _make_config(workspace=tmp_path, system_prompt="Custom system prompt")
        prompts = ClawPrompts(config=config)

        identity = prompts._get_identity()

        assert "Custom system prompt" in identity
        assert SYSTEM_PROMPT_DEFAULT not in identity

    @patch("trpc_agent_sdk.server.openclaw.agent._prompts.platform")
    def test_platform_policy_windows(self, mock_platform, tmp_path):
        mock_platform.system.return_value = "Windows"
        mock_platform.machine.return_value = "AMD64"
        mock_platform.python_version.return_value = "3.11.0"
        config = _make_config(workspace=tmp_path)
        prompts = ClawPrompts(config=config)

        identity = prompts._get_identity()

        assert "Platform Policy (Windows)" in identity

    @patch("trpc_agent_sdk.server.openclaw.agent._prompts.platform")
    def test_platform_policy_posix(self, mock_platform, tmp_path):
        mock_platform.system.return_value = "Linux"
        mock_platform.machine.return_value = "x86_64"
        mock_platform.python_version.return_value = "3.11.0"
        config = _make_config(workspace=tmp_path)
        prompts = ClawPrompts(config=config)

        identity = prompts._get_identity()

        assert "Platform Policy (POSIX)" in identity

    def test_workspace_path_in_identity(self, tmp_path):
        config = _make_config(workspace=tmp_path)
        prompts = ClawPrompts(config=config)

        identity = prompts._get_identity()

        assert str(tmp_path) in identity


class TestLoadBootstrapFiles:

    def test_no_files(self, tmp_path):
        config = _make_config(workspace=tmp_path, personal=[])
        prompts = ClawPrompts(config=config)

        result = prompts._load_bootstrap_files()
        assert result == ""

    def test_valid_files(self, tmp_path):
        bootstrap_file = tmp_path / "bootstrap.md"
        bootstrap_file.write_text("# Bootstrap Content\nHello", encoding="utf-8")

        (tmp_path / "memory").mkdir(exist_ok=True)

        config = _make_config(workspace=tmp_path, personal=[str(bootstrap_file)])
        prompts = ClawPrompts(config=config)

        result = prompts._load_bootstrap_files()
        assert "BOOTSTRAP.md" in result
        assert "Bootstrap Content" in result

    def test_missing_files(self, tmp_path):
        config = _make_config(workspace=tmp_path, personal=["/nonexistent/file.md"])
        prompts = ClawPrompts(config=config)

        result = prompts._load_bootstrap_files()
        assert result == ""

    def test_history_file_goes_to_memory_dir(self, tmp_path):
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir(exist_ok=True)

        history_file = tmp_path / "HISTORY.md"
        history_file.write_text("History content", encoding="utf-8")

        config = _make_config(workspace=tmp_path, personal=[str(history_file)])
        prompts = ClawPrompts(config=config)

        result = prompts._load_bootstrap_files()
        assert "History content" in result
        assert (mem_dir / "HISTORY.md").exists()


class TestBuildSystemPrompt:

    def test_combines_identity_and_bootstrap(self, tmp_path):
        bootstrap_file = tmp_path / "notes.md"
        bootstrap_file.write_text("Important notes", encoding="utf-8")

        config = _make_config(workspace=tmp_path, personal=[str(bootstrap_file)])
        prompts = ClawPrompts(config=config)

        result = prompts.build_system_prompt()

        assert BOT_NAME in result
        assert "Important notes" in result
        assert "---" in result

    def test_no_bootstrap_just_identity(self, tmp_path):
        config = _make_config(workspace=tmp_path, personal=[])
        prompts = ClawPrompts(config=config)

        result = prompts.build_system_prompt()

        assert BOT_NAME in result
        assert "Guidelines" in result


class TestClawPromptsInit:

    def test_stores_config(self, tmp_path):
        config = _make_config(workspace=tmp_path)
        prompts = ClawPrompts(config=config, silent=True)
        assert prompts.config is config
        assert prompts.silent is True
        assert prompts.workspace == tmp_path
