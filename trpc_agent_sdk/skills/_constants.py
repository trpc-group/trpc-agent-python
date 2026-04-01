# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Constants for TRPC Agent skills system.

This module defines constants for the skills system.
"""

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

# State key for docs of skills
SKILL_DOCS_STATE_KEY_PREFIX = "temp:skill:docs:"
"""State key for docs of skills."""

# State key for tools of skills
SKILL_TOOLS_STATE_KEY_PREFIX = "temp:skill:tools:"
"""State key for tools of skills."""

# EnvSkillsCacheDir overrides where URL-based skills roots are cached.
# When empty, the user cache directory is used.
ENV_SKILLS_CACHE_DIR = "SKILLS_CACHE_DIR"
"""Environment variable name for skills cache directory."""
