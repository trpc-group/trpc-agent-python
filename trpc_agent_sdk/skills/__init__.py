# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""TRPC Agent Skills System.

This module provides a skills system inspired by Anthropic's skills pattern,
allowing agents to dynamically load and use specialized capabilities.

Skills are self-contained directories that include:
- SKILL.md: Metadata and instructions in YAML frontmatter
- Scripts and resources
- Tool definitions and implementations

Example:
    >>> from trpc_agent_sdk.skills import SkillRegistry, SkillToolSet
    >>> registry = SkillRegistry()
    >>> # Register skills...
    >>> toolset = SkillToolSet()
    >>> tools = await toolset.get_tools()
"""

from ._common import SelectionMode
from ._common import docs_scan_prefix
from ._common import docs_state_key
from ._common import generic_get_selection
from ._common import loaded_order_state_key
from ._common import loaded_scan_prefix
from ._common import loaded_state_key
from ._common import tool_scan_prefix
from ._common import tool_state_key
from ._common import use_session_skill_state
from ._constants import ENV_SKILLS_ROOT
from ._constants import SKILL_ARTIFACTS_STATE_KEY
from ._constants import SKILL_DOCS_BY_AGENT_STATE_KEY_PREFIX
from ._constants import SKILL_DOCS_STATE_KEY_PREFIX
from ._constants import SKILL_FILE
from ._constants import SKILL_LOADED_BY_AGENT_STATE_KEY_PREFIX
from ._constants import SKILL_LOADED_ORDER_BY_AGENT_STATE_KEY_PREFIX
from ._constants import SKILL_LOADED_ORDER_STATE_KEY_PREFIX
from ._constants import SKILL_LOADED_STATE_KEY_PREFIX
from ._constants import SKILL_LOAD_MODE_VALUES
from ._constants import SKILL_REGISTRY_KEY
from ._constants import SKILL_REPOSITORY_KEY
from ._constants import SKILL_TOOLS_BY_AGENT_STATE_KEY_PREFIX
from ._constants import SKILL_TOOLS_NAMES
from ._constants import SKILL_TOOLS_STATE_KEY_PREFIX
from ._constants import SkillLoadModeNames
from ._constants import SkillProfileNames
from ._constants import SkillToolsNames
from ._dynamic_toolset import DynamicSkillToolSet
from ._registry import SkillRegistry
from ._repository import BaseSkillRepository
from ._repository import FsSkillRepository
from ._repository import VisibilityFilter
from ._repository import create_default_skill_repository
from ._skill_config import get_skill_config
from ._skill_config import get_skill_load_mode
from ._skill_config import set_skill_config
from ._skill_profile import SkillProfileFlags
from ._state_keys import docs_key
from ._state_keys import docs_prefix
from ._state_keys import docs_session_key
from ._state_keys import docs_session_prefix
from ._state_keys import loaded_key
from ._state_keys import loaded_order_key
from ._state_keys import loaded_prefix
from ._state_keys import loaded_session_key
from ._state_keys import loaded_session_order_key
from ._state_keys import loaded_session_prefix
from ._state_keys import tool_key
from ._state_keys import tool_prefix
from ._state_keys import tool_session_key
from ._state_keys import tool_session_prefix
from ._state_migration import SKILLS_LEGACY_MIGRATION_STATE_KEY
from ._state_migration import maybe_migrate_legacy_skill_state
from ._state_order import marshal_loaded_order
from ._state_order import parse_loaded_order
from ._state_order import touch_loaded_order
from ._toolset import SkillToolSet
from ._types import Skill
from ._types import SkillConfig
from ._types import SkillFrontMatter
from ._types import SkillRequires
from ._types import SkillResource
from ._types import SkillSummary
from ._url_root import ArchiveExtractor
from ._url_root import SkillRootResolver
from ._utils import get_state_delta
from ._utils import set_state_delta
from .tools import SkillLoadTool
from .tools import SkillRunTool
from .tools import skill_list
from .tools import skill_list_docs
from .tools import skill_list_tools
from .tools import skill_select_docs
from .tools import skill_select_tools

__all__ = [
    "SelectionMode",
    "docs_scan_prefix",
    "docs_state_key",
    "generic_get_selection",
    "loaded_order_state_key",
    "loaded_scan_prefix",
    "loaded_state_key",
    "tool_scan_prefix",
    "tool_state_key",
    "use_session_skill_state",
    "ENV_SKILLS_ROOT",
    "SKILL_ARTIFACTS_STATE_KEY",
    "SKILL_DOCS_BY_AGENT_STATE_KEY_PREFIX",
    "SKILL_DOCS_STATE_KEY_PREFIX",
    "SKILL_FILE",
    "SKILL_LOADED_BY_AGENT_STATE_KEY_PREFIX",
    "SKILL_LOADED_ORDER_BY_AGENT_STATE_KEY_PREFIX",
    "SKILL_LOADED_ORDER_STATE_KEY_PREFIX",
    "SKILL_LOADED_STATE_KEY_PREFIX",
    "SKILL_LOAD_MODE_VALUES",
    "SKILL_REGISTRY_KEY",
    "SKILL_REPOSITORY_KEY",
    "SKILL_TOOLS_BY_AGENT_STATE_KEY_PREFIX",
    "SKILL_TOOLS_NAMES",
    "SKILL_TOOLS_STATE_KEY_PREFIX",
    "SkillLoadModeNames",
    "SkillProfileNames",
    "SkillToolsNames",
    "DynamicSkillToolSet",
    "SkillRegistry",
    "BaseSkillRepository",
    "FsSkillRepository",
    "VisibilityFilter",
    "create_default_skill_repository",
    "get_skill_config",
    "get_skill_load_mode",
    "set_skill_config",
    "SkillProfileFlags",
    "docs_key",
    "docs_prefix",
    "docs_session_key",
    "docs_session_prefix",
    "loaded_key",
    "loaded_order_key",
    "loaded_prefix",
    "loaded_session_key",
    "loaded_session_order_key",
    "loaded_session_prefix",
    "tool_key",
    "tool_prefix",
    "tool_session_key",
    "tool_session_prefix",
    "SKILLS_LEGACY_MIGRATION_STATE_KEY",
    "maybe_migrate_legacy_skill_state",
    "marshal_loaded_order",
    "parse_loaded_order",
    "touch_loaded_order",
    "SkillToolSet",
    "Skill",
    "SkillConfig",
    "SkillFrontMatter",
    "SkillRequires",
    "SkillResource",
    "SkillSummary",
    "ArchiveExtractor",
    "SkillRootResolver",
    "get_state_delta",
    "set_state_delta",
    "SkillLoadTool",
    "SkillRunTool",
    "skill_list",
    "skill_list_docs",
    "skill_list_tools",
    "skill_select_docs",
    "skill_select_tools",
]
