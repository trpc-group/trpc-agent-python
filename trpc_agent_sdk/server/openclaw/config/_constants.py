# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Constants for trpc-claw."""

from pathlib import Path

BOT_NAME: str = "trpc-claw-py"
"""Bot name."""
DEFAULT_TRPC_AGENT_CLAW_DIR: Path = Path.home() / ".trpc_agent_claw"
"""Default trpc-claw directory."""
DEFAULT_CONFIG_PATH: Path = DEFAULT_TRPC_AGENT_CLAW_DIR / "config.yaml"
"""Default config path."""
DEFAULT_WORKSPACE_PATH: Path = DEFAULT_TRPC_AGENT_CLAW_DIR / "workspace"
"""Default workspace path."""
DEFAULT_HISTORY_PATH: Path = DEFAULT_TRPC_AGENT_CLAW_DIR / "history"
"""Default history path."""
DEFAULT_CLI_HISTORY_PATH: Path = DEFAULT_HISTORY_PATH / "cli_history"
"""Default CLI history path."""
DEFAULT_BRIDGE_INSTALL_DIR: Path = DEFAULT_TRPC_AGENT_CLAW_DIR / "bridge"
"""Default bridge install directory."""
DEFAULT_LEGACY_SESSIONS_DIR: Path = DEFAULT_TRPC_AGENT_CLAW_DIR / "sessions"
"""Legacy sessions directory."""
TRPC_AGENT_CLAW_CONFIG: str = "TRPC_AGENT_CLAW_CONFIG"
"""TRPC_AGENT_CLAW_CONFIG environment variable."""

DEFAULT_APP_NAME: str = "trpc_claw_py"
"""Default app name."""
DEFAULT_USER_ID: str = "trpc_claw_user"
"""Default user id."""

TRPC_CLAW_SKILLS_INSTALL_ROOT_ENV_NAME: str = "TRPC_CLAW_SKILLS_INSTALL_ROOT"
"""TRPC_CLAW_SKILLS_INSTALL_ROOT environment variable."""

HISTORY_FILE_NAME: str = "HISTORY.md"
"""History file name."""
MEMORY_FILE_NAME: str = "MEMORY.md"
"""Memory file name."""
SOUL_FILE_NAME: str = "SOUL.md"
"""Soul file name."""
USER_FILE_NAME: str = "USER.md"
"""User file name."""
TOOL_FILE_NAME: str = "TOOLS.md"
"""Tools file name."""
AGENT_FILE_NAME: str = "AGENTS.md"
"""Agent file name."""
