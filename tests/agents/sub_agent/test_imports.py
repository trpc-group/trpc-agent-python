# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Smoke tests for the dynamic sub-agent package import surface."""

from __future__ import annotations

import sys


def test_public_imports() -> None:
    from trpc_agent_sdk.agents.sub_agent import (
        SpawnSubAgentTool,
        DEFAULT_AGENT,
        EXPLORE_AGENT,
        GENERAL_PURPOSE_AGENT,
        PLAN_AGENT,
        DynamicSubAgentTool,
        SubAgentArchetype,
        SubAgentRegistry,
    )
    assert SpawnSubAgentTool is not None
    assert DynamicSubAgentTool is not None
    assert SubAgentArchetype is not None
    assert SubAgentRegistry is not None
    assert DEFAULT_AGENT.name == "default"
    assert GENERAL_PURPOSE_AGENT.name == "general-purpose"
    assert EXPLORE_AGENT.name == "Explore"
    assert PLAN_AGENT.name == "Plan"


def test_dynamic_not_loaded_when_only_agents_imported() -> None:
    """Importing trpc_agent_sdk.agents must not eagerly pull in ``sub_agent``.

    The sub_agent subsystem brings in file_tools / web tools — keep those off
    the default agents import path.
    """
    # Drop any cached entries for a clean check; sub-modules already loaded
    # by other tests in this run would otherwise pollute the result.
    saved = {}
    for mod in list(sys.modules):
        if mod.startswith("trpc_agent_sdk.agents.sub_agent"):
            saved[mod] = sys.modules.pop(mod)

    try:
        import trpc_agent_sdk.agents  # noqa: F401
        assert "trpc_agent_sdk.agents.sub_agent" not in sys.modules
    finally:
        # Restore deleted modules so subsequent tests don't get stale
        # class objects from re-imports (e.g. _BorrowedToolSet).
        sys.modules.update(saved)


def test_lazy_import_dynamic_sub_agent_tool_from_tools() -> None:
    """DynamicSubAgentTool is lazily re-exported from trpc_agent_sdk.tools."""
    from trpc_agent_sdk.tools import DynamicSubAgentTool
    from trpc_agent_sdk.agents.sub_agent import DynamicSubAgentTool as DirectTool
    assert DynamicSubAgentTool is DirectTool


def test_lazy_import_spawn_sub_agent_tool_from_tools() -> None:
    """SpawnSubAgentTool is lazily re-exported from trpc_agent_sdk.tools."""
    from trpc_agent_sdk.tools import SpawnSubAgentTool
    from trpc_agent_sdk.agents.sub_agent import SpawnSubAgentTool as DirectTool
    assert SpawnSubAgentTool is DirectTool


def test_tools_dir_includes_dynamic_subagents() -> None:
    """__dir__ of trpc_agent_sdk.tools includes lazy re-exports."""
    import trpc_agent_sdk.tools
    names = dir(trpc_agent_sdk.tools)
    assert "DynamicSubAgentTool" in names
    assert "SpawnSubAgentTool" in names


def test_tools_getattr_unknown_raises() -> None:
    """__getattr__ raises AttributeError for unknown names."""
    import trpc_agent_sdk.tools
    try:
        _ = trpc_agent_sdk.tools.__getattr__("NonExistentTool")
        assert False, "should have raised"
    except AttributeError:
        pass
