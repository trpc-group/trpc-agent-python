# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Dynamic sub-agent subsystem.

Public API:
    - ``DynamicAgentTool`` — LLM-defined sub-agent: specify ``instruction`` at
      call time to create any specialist on the fly. Inherits parent tools.
    - ``SpawnSubAgentTool`` — catalog-based dispatch: LLM selects from
      pre-registered archetypes (each with locked instruction and tool set).
      Supports MD-file authoring via ``agent_paths``.
    - ``SubAgentArchetype`` / ``SubAgentRegistry`` — define and register archetypes.
    - ``DEFAULT_AGENT`` — neutral built-in archetype, auto-registered by
      ``SpawnSubAgentTool``.
    - ``GENERAL_PURPOSE_AGENT`` / ``EXPLORE_AGENT`` / ``PLAN_AGENT`` —
      opt-in built-in archetypes (researcher / read-only search / read-only
      planning). Pass them via ``agents=[...]`` to ``SpawnSubAgentTool``.
    - ``load_archetypes_from_dir`` / ``load_archetype_from_file`` — load archetypes
      from ``.md`` files on disk (pass ``agent_paths`` to ``SpawnSubAgentTool``).

This package is **not** re-exported from ``trpc_agent_sdk.agents`` to keep the
default agents import path free of file_tools / web tools dependencies.
"""

from ._archetype import SubAgentArchetype
from ._defaults import DEFAULT_AGENT
from ._defaults import EXPLORE_AGENT
from ._defaults import GENERAL_PURPOSE_AGENT
from ._defaults import PLAN_AGENT
from ._dynamic_agent_tool import DynamicAgentTool
from ._loader import load_archetype_from_file
from ._loader import load_archetypes_from_dir
from ._registry import SubAgentRegistry
from ._spawn_sub_agent_tool import SpawnSubAgentTool
from ._sub_agent_config import SubAgentConfig

__all__ = [
    "DynamicAgentTool",
    "SpawnSubAgentTool",
    "SubAgentArchetype",
    "SubAgentRegistry",
    "DEFAULT_AGENT",
    "GENERAL_PURPOSE_AGENT",
    "EXPLORE_AGENT",
    "PLAN_AGENT",
    "SubAgentConfig",
    "load_archetype_from_file",
    "load_archetypes_from_dir",
]
