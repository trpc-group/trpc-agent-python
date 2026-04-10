# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""State key builders for skills."""

from __future__ import annotations

from urllib.parse import quote

from ._constants import SKILL_DOCS_BY_AGENT_STATE_KEY_PREFIX
from ._constants import SKILL_DOCS_STATE_KEY_PREFIX
from ._constants import SKILL_LOADED_BY_AGENT_STATE_KEY_PREFIX
from ._constants import SKILL_LOADED_ORDER_BY_AGENT_STATE_KEY_PREFIX
from ._constants import SKILL_LOADED_ORDER_STATE_KEY_PREFIX
from ._constants import SKILL_LOADED_STATE_KEY_PREFIX
from ._constants import SKILL_TOOLS_BY_AGENT_STATE_KEY_PREFIX
from ._constants import SKILL_TOOLS_STATE_KEY_PREFIX

_STATE_KEY_SCOPE_DELIMITER = "/"
_STATE_KEY_TEMP_PREFIX = "temp:"


def _escape_scope_segment(value: str) -> str:
    """Escape a scoped-key segment when it contains the delimiter."""
    if _STATE_KEY_SCOPE_DELIMITER in value:
        return quote(value, safe="")
    return value


def to_persistent_prefix(prefix: str) -> str:
    """Convert temp-prefixed state prefix to persistent prefix."""
    if prefix.startswith(_STATE_KEY_TEMP_PREFIX):
        return prefix[len(_STATE_KEY_TEMP_PREFIX):]
    return prefix


def loaded_key(agent_name: str, skill_name: str) -> str:
    """Return the loaded-state key for a skill.

    When ``agent_name`` is empty, fallback to the legacy unscoped key.
    """
    agent_name = agent_name.strip()
    skill_name = skill_name.strip()
    if not agent_name:
        return f"{SKILL_LOADED_STATE_KEY_PREFIX}{skill_name}"
    return (f"{SKILL_LOADED_BY_AGENT_STATE_KEY_PREFIX}"
            f"{_escape_scope_segment(agent_name)}"
            f"{_STATE_KEY_SCOPE_DELIMITER}{skill_name}")


def loaded_session_key(keys: str) -> str:
    """Return persistent loaded-state key for session mode."""
    return to_persistent_prefix(keys)


def docs_key(agent_name: str, skill_name: str) -> str:
    """Return the docs-state key for a skill.

    When ``agent_name`` is empty, fallback to the legacy unscoped key.
    """
    agent_name = agent_name.strip()
    skill_name = skill_name.strip()
    if not agent_name:
        return f"{SKILL_DOCS_STATE_KEY_PREFIX}{skill_name}"
    return (f"{SKILL_DOCS_BY_AGENT_STATE_KEY_PREFIX}"
            f"{_escape_scope_segment(agent_name)}"
            f"{_STATE_KEY_SCOPE_DELIMITER}{skill_name}")


def docs_session_key(keys: str) -> str:
    """Return persistent docs-state key for session mode."""
    return to_persistent_prefix(keys)


def tool_key(agent_name: str, skill_name: str) -> str:
    """Return the tools-state key for a skill.

    When ``agent_name`` is empty, fallback to the legacy unscoped key.
    """
    agent_name = agent_name.strip()
    skill_name = skill_name.strip()
    if not agent_name:
        return f"{SKILL_TOOLS_STATE_KEY_PREFIX}{skill_name}"
    return (f"{SKILL_TOOLS_BY_AGENT_STATE_KEY_PREFIX}"
            f"{_escape_scope_segment(agent_name)}"
            f"{_STATE_KEY_SCOPE_DELIMITER}{skill_name}")


def tool_session_key(keys: str) -> str:
    """Return persistent tools-state key for session mode."""
    return to_persistent_prefix(keys)


def loaded_prefix(agent_name: str) -> str:
    """Return the loaded-state scan prefix for an agent."""
    agent_name = agent_name.strip()
    if not agent_name:
        return SKILL_LOADED_STATE_KEY_PREFIX
    return (f"{SKILL_LOADED_BY_AGENT_STATE_KEY_PREFIX}"
            f"{_escape_scope_segment(agent_name)}"
            f"{_STATE_KEY_SCOPE_DELIMITER}")


def loaded_session_prefix(keys: str) -> str:
    """Return persistent loaded-state scan prefix for session mode."""
    return to_persistent_prefix(keys)


def docs_prefix(agent_name: str) -> str:
    """Return the docs-state scan prefix for an agent."""
    agent_name = agent_name.strip()
    if not agent_name:
        return SKILL_DOCS_STATE_KEY_PREFIX
    return (f"{SKILL_DOCS_BY_AGENT_STATE_KEY_PREFIX}"
            f"{_escape_scope_segment(agent_name)}"
            f"{_STATE_KEY_SCOPE_DELIMITER}")


def docs_session_prefix(keys: str) -> str:
    """Return persistent docs-state scan prefix for session mode."""
    return to_persistent_prefix(keys)


def tool_prefix(agent_name: str) -> str:
    """Return the tools-state scan prefix for an agent."""
    agent_name = agent_name.strip()
    if not agent_name:
        return SKILL_TOOLS_STATE_KEY_PREFIX
    return (f"{SKILL_TOOLS_BY_AGENT_STATE_KEY_PREFIX}"
            f"{_escape_scope_segment(agent_name)}"
            f"{_STATE_KEY_SCOPE_DELIMITER}")


def tool_session_prefix(keys: str) -> str:
    """Return persistent tools-state scan prefix for session mode."""
    return to_persistent_prefix(keys)


def loaded_order_key(agent_name: str) -> str:
    """Return the loaded-order key for an agent."""
    agent_name = agent_name.strip()
    if not agent_name:
        return SKILL_LOADED_ORDER_STATE_KEY_PREFIX
    return f"{SKILL_LOADED_ORDER_BY_AGENT_STATE_KEY_PREFIX}{_escape_scope_segment(agent_name)}"


def loaded_session_order_key(keys: str) -> str:
    """Return persistent loaded-order key for session mode."""
    return to_persistent_prefix(keys)
