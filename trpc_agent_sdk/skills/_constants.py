# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Constants for TRPC Agent skills system.

This module defines constants for the skills system.
"""

from enum import Enum

SKILL_FILE = "SKILL.md"

# Environment variable name for skills root directory
ENV_SKILLS_ROOT = "SKILLS_ROOT"

# Metadata key for skill name
SKILL_REGISTRY_KEY = "__trpc_agent_skills_registry"
"""Metadata key for skill registry."""

SKILL_REPOSITORY_KEY = "__trpc_agent_skills_repository"
"""Key for skill repository."""

# State key for loaded skills
SKILL_LOADED_STATE_KEY_PREFIX = "temp:skill:loaded:"
"""State key for loaded skills."""

# State key for loaded skills scoped by agent
SKILL_LOADED_BY_AGENT_STATE_KEY_PREFIX = "temp:skill:loaded_by_agent:"
"""State key prefix for loaded skills scoped by agent."""

# State key for docs of skills
SKILL_DOCS_STATE_KEY_PREFIX = "temp:skill:docs:"
"""State key for docs of skills."""

# State key for docs of skills scoped by agent
SKILL_DOCS_BY_AGENT_STATE_KEY_PREFIX = "temp:skill:docs_by_agent:"
"""State key prefix for docs scoped by agent."""

# State key for loaded skill order
SKILL_LOADED_ORDER_STATE_KEY_PREFIX = "temp:skill:loaded_order:"
"""State key prefix for loaded skill touch order."""

# State key for loaded skill order scoped by agent
SKILL_LOADED_ORDER_BY_AGENT_STATE_KEY_PREFIX = "temp:skill:loaded_order_by_agent:"
"""State key prefix for loaded skill touch order scoped by agent."""

# State key for tools of skills
SKILL_TOOLS_STATE_KEY_PREFIX = "temp:skill:tools:"
"""State key for tools of skills."""

# State key for tools of skills scoped by agent
SKILL_TOOLS_BY_AGENT_STATE_KEY_PREFIX = "temp:skill:tools_by_agent:"
"""State key prefix for tools scoped by agent."""

# State key for per-tool-call artifact refs (replay support)
SKILL_ARTIFACTS_STATE_KEY = "temp:skill:artifacts"
"""State key for skill tool-call artifact references."""

# EnvSkillsCacheDir overrides where URL-based skills roots are cached.
# When empty, the user cache directory is used.
ENV_SKILLS_CACHE_DIR = "SKILLS_CACHE_DIR"
"""Environment variable name for skills cache directory."""


class SkillProfileNames(str, Enum):
    FULL = "full"
    KNOWLEDGE_ONLY = "knowledge_only"

    def __str__(self) -> str:
        return self.value


class SkillToolsNames(str, Enum):
    LOAD = "skill_load"
    SELECT_DOCS = "skill_select_docs"
    LIST_DOCS = "skill_list_docs"
    RUN = "skill_run"
    SELECT_TOOLS = "skill_select_tools"
    LIST_SKILLS = "skill_list_skills"
    EXEC = "skill_exec"
    WRITE_STDIN = "skill_write_stdin"
    POLL_SESSION = "skill_poll_session"
    KILL_SESSION = "skill_kill_session"

    def __str__(self) -> str:
        return self.value


SKILL_TOOLS_NAMES = [tool.value for tool in SkillToolsNames.__members__.values()]


class SkillLoadModeNames(str, Enum):
    ONCE = "once"
    TURN = "turn"
    SESSION = "session"

    def __str__(self) -> str:
        return self.value


SKILL_LOAD_MODE_VALUES = [mode.value for mode in SkillLoadModeNames.__members__.values()]

SKILL_CONFIG_KEY = "__trpc_agent_skills_config"
