# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Constants for trpc_claw."""

from pathlib import Path

BOT_NAME: str = "trpc_claw"
"""Bot name."""
DEFAULT_TRPC_CLAW_DIR: Path = Path.home() / ".trpc_claw"
"""Default trpc_claw directory."""
DEFAULT_CONFIG_PATH: Path = DEFAULT_TRPC_CLAW_DIR / "config.yaml"
"""Default config path for trpc_claw."""
DEFAULT_WORKSPACE_PATH: Path = DEFAULT_TRPC_CLAW_DIR / "workspace"
"""Default workspace path for trpc_claw."""
DEFAULT_LEGACY_SESSIONS_DIR: Path = DEFAULT_TRPC_CLAW_DIR / "sessions"
"""Legacy sessions directory for trpc_claw."""
TRPC_CLAW_CONFIG: str = "TRPC_CLAW_CONFIG"
"""TRPC_CLAW_CONFIG environment variable for trpc_claw."""

DEFAULT_APP_NAME: str = "trpc_claw_py"
"""Default app name for trpc_claw."""
DEFAULT_USER_ID: str = "trpc_claw_user"
"""Default user id for trpc_claw."""

TRPC_CLAW_SKILLS_INSTALL_ROOT_ENV_NAME: str = "TRPC_CLAW_SKILLS_INSTALL_ROOT"
"""TRPC_CLAW_SKILLS_INSTALL_ROOT environment variable for trpc_claw."""

HISTORY_FILE_NAME: str = "HISTORY.md"
"""History file name for trpc_claw."""
MEMORY_FILE_NAME: str = "MEMORY.md"
"""Memory file name for trpc_claw."""
SOUL_FILE_NAME: str = "SOUL.md"
"""Soul file name for trpc_claw."""
USER_FILE_NAME: str = "USER.md"
"""User file name for trpc_claw."""
TOOL_FILE_NAME: str = "TOOLS.md"
"""Tools file name for trpc_claw."""
AGENT_FILE_NAME: str = "AGENTS.md"
"""Agent file name for trpc_claw."""
