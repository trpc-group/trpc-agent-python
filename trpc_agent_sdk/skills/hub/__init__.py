# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Skills Hub — source adapters for discovering and fetching agent skills.

Provides:

  - `SkillSource`: the abstract base class all adapters subclass.
  - `SkillMeta` / `SkillBundle`: the data types adapters exchange.
  - `SkillSpec`: a declaration for installing a fetched skill locally.
  - Path validators (`validate_skill_name`, `validate_category_name`,
    `validate_bundle_rel_path`) used when writing fetched bundles to disk.
  - `GitHubAuth`: GitHub API authentication via an explicitly injected PAT.
  - Seven concrete adapters: `GitHubSource`, `WellKnownSkillSource`,
    `HermesIndexSource`, `SkillsShSource`, `ClawHubSource`,
    `ClaudeMarketplaceSource`, `LobeHubSource`.
"""

from ._claude_marketplace import ClaudeMarketplaceSource
from ._clawhub import ClawHubSource
from ._github import GitHubAuth
from ._github import GitHubSource
from ._hermes_index import HermesIndexSource
from ._install import SkillSpec
from ._install import SkillSpecsConfig
from ._install import async_sync_remote_skills
from ._install import sync_remote_skills
from ._lobehub import LobeHubSource
from ._skills_sh import SkillsShSource
from ._source import SkillSource
from ._types import SkillBundle
from ._types import SkillMeta
from ._types import validate_bundle_rel_path
from ._types import validate_category_name
from ._types import validate_skill_name
from ._well_known import WellKnownSkillSource

__all__ = [
    "SkillSource",
    "SkillMeta",
    "SkillBundle",
    "SkillSpec",
    "SkillSpecsConfig",
    "sync_remote_skills",
    "async_sync_remote_skills",
    "validate_bundle_rel_path",
    "validate_category_name",
    "validate_skill_name",
    "GitHubAuth",
    "GitHubSource",
    "WellKnownSkillSource",
    "SkillsShSource",
    "ClawHubSource",
    "ClaudeMarketplaceSource",
    "LobeHubSource",
    "HermesIndexSource",
]
