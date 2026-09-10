# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Provide setup entry points for long-term memory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import TYPE_CHECKING

from trpc_agent_sdk.sessions.compact._runtime import AdvancedMemoryRuntime

from ._memory_context import LongTermMemoryContext
from ._memory_context import setup_long_term_memory_context

if TYPE_CHECKING:
    from trpc_agent_sdk.agents import LlmAgent
    from trpc_agent_sdk.tools._advanced_memory_tool import AdvancedMemoryTools


@dataclass(frozen=True)
class LongTermMemoryIntegration:
    """Aggregate the long-term memory callback and tools."""

    context: LongTermMemoryContext
    tools: "AdvancedMemoryTools | None"


def _setup_long_term_memory_tools(
    agent: "LlmAgent",
    memory_runtime: AdvancedMemoryRuntime,
) -> "AdvancedMemoryTools":
    """Install the three official memory tools idempotently."""
    from trpc_agent_sdk.tools._advanced_memory_tool import (
        ADVANCED_MEMORY_TOOL_NAMES,
    )
    from trpc_agent_sdk.tools._advanced_memory_tool import AdvancedMemoryTools

    matching_tools = [
        tool
        for tool in agent.tools
        if getattr(tool, "name", None) in ADVANCED_MEMORY_TOOL_NAMES
    ]
    if matching_tools:
        owners = {
            getattr(getattr(tool, "func", None), "__self__", None)
            for tool in matching_tools
        }
        if len(owners) != 1:
            raise ValueError(
                "Advanced Memory tool names are already used by different tools"
            )
        owner = owners.pop()
        if not isinstance(owner, AdvancedMemoryTools):
            raise ValueError(
                "Advanced Memory tool names are already used by non-SDK tools"
            )
        if owner.runtime is not memory_runtime:
            raise ValueError("Advanced Memory tools use another runtime")
        installed_names = {
            getattr(tool, "name", None) for tool in matching_tools
        }
        if installed_names != ADVANCED_MEMORY_TOOL_NAMES:
            raise ValueError(
                "Advanced Memory tools are only partially installed"
            )
        return owner
    tools = AdvancedMemoryTools(memory_runtime)
    agent.tools.extend(tools.as_tools())
    return tools


def _setup_preload_memory_tool(
    agent: "LlmAgent",
    memory_runtime: AdvancedMemoryRuntime,
    model: Any | None = None,
) -> None:
    """Install the automatic topic-memory preprocessor when enabled."""
    if (
        not memory_runtime.config.enabled
        or not memory_runtime.config.preload_memory_enabled
    ):
        return
    from trpc_agent_sdk.tools import PreloadMemoryTool

    from ._preload_memory import MemoryPreloader
    from ._preload_memory import ModelMemoryRelevanceSelector

    existing = [
        tool
        for tool in agent.tools
        if getattr(tool, "name", None) == "preload_memory"
    ]
    use_legacy_memory = False
    if existing:
        if len(existing) != 1 or not isinstance(existing[0], PreloadMemoryTool):
            raise ValueError(
                "Advanced Memory preload tool name is already used by another tool"
            )
        use_legacy_memory = existing[0].uses_legacy_memory
        agent.tools.remove(existing[0])
    preloader = MemoryPreloader(
        memory_runtime,
        ModelMemoryRelevanceSelector(model),
    )
    agent.tools.append(
        PreloadMemoryTool(
            memory_preloader=preloader.preload,
            use_legacy_memory=use_legacy_memory,
        )
    )


def setup_long_term_memory(
    agent: "LlmAgent",
    memory_runtime: AdvancedMemoryRuntime,
    *,
    preload_memory_model: Any | None = None,
    install_tools: bool = True,
) -> LongTermMemoryIntegration:
    """Install only user-scoped long-term memory behavior."""
    context = setup_long_term_memory_context(agent, memory_runtime)
    tools = (
        _setup_long_term_memory_tools(agent, memory_runtime)
        if install_tools and memory_runtime.config.enabled
        else None
    )
    _setup_preload_memory_tool(
        agent,
        memory_runtime,
        model=preload_memory_model,
    )
    return LongTermMemoryIntegration(context=context, tools=tools)
