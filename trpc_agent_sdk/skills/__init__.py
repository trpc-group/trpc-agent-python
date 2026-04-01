# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""TRPC Agent Skills package."""

from ._common import BaseSelectionResult
from ._common import SelectionMode
from ._common import add_selection
from ._common import clear_selection
from ._common import generic_get_selection
from ._common import generic_select_items
from ._common import get_previous_selection
from ._common import get_state_delta_value
from ._common import replace_selection
from ._common import set_state_delta_for_selection
from ._constants import ENV_SKILLS_ROOT
from ._constants import SKILL_DOCS_STATE_KEY_PREFIX
from ._constants import SKILL_FILE
from ._constants import SKILL_LOADED_STATE_KEY_PREFIX
from ._constants import SKILL_REGISTRY_KEY
from ._constants import SKILL_REPOSITORY_KEY
from ._constants import SKILL_TOOLS_STATE_KEY_PREFIX
from ._dynamic_toolset import DynamicSkillToolSet
from ._registry import SKILL_REGISTRY
from ._registry import SkillRegistry
from ._repository import BaseSkillRepository
from ._repository import FsSkillRepository
from ._repository import create_default_skill_repository
from ._run_tool import ArtifactInfo
from ._run_tool import SkillRunInput
from ._run_tool import SkillRunOutput
from ._run_tool import SkillRunTool
from ._tools import SkillSelectDocsResult
from ._tools import SkillSelectToolsResult
from ._tools import skill_list
from ._tools import skill_list_docs
from ._tools import skill_list_tools
from ._tools import skill_load
from ._tools import skill_select_docs
from ._tools import skill_select_tools
from ._toolset import SkillToolSet
from ._types import Skill
from ._types import SkillMetadata
from ._types import SkillResource
from ._types import SkillSummary
from ._types import SkillWorkspaceInputRecord
from ._types import SkillWorkspaceMetadata
from ._types import SkillWorkspaceOutputRecord
from ._types import format_datetime
from ._types import parse_datetime
from ._url_root import ArchiveExt
from ._url_root import ArchiveExtractor
from ._url_root import ArchiveKind
from ._url_root import CacheConfig
from ._url_root import FilePerm
from ._url_root import SizeLimit
from ._url_root import SkillRootResolver
from ._url_root import TarPerm
from ._utils import compute_dir_digest
from ._utils import ensure_layout
from ._utils import get_state_delta
from ._utils import load_metadata
from ._utils import save_metadata
from ._utils import set_state_delta
from ._utils import shell_quote

__all__ = [
    "BaseSelectionResult",
    "SelectionMode",
    "add_selection",
    "clear_selection",
    "generic_get_selection",
    "generic_select_items",
    "get_previous_selection",
    "get_state_delta_value",
    "replace_selection",
    "set_state_delta_for_selection",
    "ENV_SKILLS_ROOT",
    "SKILL_DOCS_STATE_KEY_PREFIX",
    "SKILL_FILE",
    "SKILL_LOADED_STATE_KEY_PREFIX",
    "SKILL_REGISTRY_KEY",
    "SKILL_REPOSITORY_KEY",
    "SKILL_TOOLS_STATE_KEY_PREFIX",
    "DynamicSkillToolSet",
    "SkillRegistry",
    "SKILL_REGISTRY",
    "BaseSkillRepository",
    "FsSkillRepository",
    "create_default_skill_repository",
    "ArtifactInfo",
    "SkillRunInput",
    "SkillRunOutput",
    "SkillRunTool",
    "SkillSelectDocsResult",
    "SkillSelectToolsResult",
    "skill_list",
    "skill_list_docs",
    "skill_list_tools",
    "skill_load",
    "skill_select_docs",
    "skill_select_tools",
    "SkillToolSet",
    "Skill",
    "SkillMetadata",
    "SkillResource",
    "SkillSummary",
    "SkillWorkspaceInputRecord",
    "SkillWorkspaceMetadata",
    "SkillWorkspaceOutputRecord",
    "format_datetime",
    "parse_datetime",
    "ArchiveExt",
    "ArchiveExtractor",
    "ArchiveKind",
    "CacheConfig",
    "FilePerm",
    "SizeLimit",
    "SkillRootResolver",
    "TarPerm",
    "compute_dir_digest",
    "ensure_layout",
    "get_state_delta",
    "load_metadata",
    "save_metadata",
    "set_state_delta",
    "shell_quote",
]
