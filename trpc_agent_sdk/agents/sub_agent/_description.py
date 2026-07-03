# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Render the spawn_subagent tool description from a SubAgentRegistry.

The rendered description embeds each archetype's name, description text,
and a ``(Tools: ...)`` suffix derived from its tool list — giving the
parent LLM both the selection guidance and the capability boundary in a
single block.
"""

from __future__ import annotations

from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import BaseToolSet

from ._archetype import SubAgentArchetype
from ._registry import SubAgentRegistry

_HEADER = """\
Launch a new sub-agent to handle complex, multi-step tasks.
Each sub-agent type has specific capabilities and tools available to it.

Available subagent types:
"""

_FOOTER = """

IMPORTANT: The sub-agent cannot spawn further sub-agents.
"""


def tool_names_of(archetype: SubAgentArchetype) -> list[str]:
    """Extract human-readable tool names from an archetype's tool list.

    - ``BaseTool`` instance: use ``instance.name``.
    - ``BaseToolSet`` instance: use the class name (v1 simplification — we do
      not expand the toolset to its individual tool names because that would
      require an async call).
    - Class reference (factory): instantiate to get the real ``name``.
    - Other callable: use ``__name__`` if available, else ``repr``.
    """
    out: list[str] = []
    if archetype.tools is None:
        return ["(all)"]
    for t in archetype.tools:
        if isinstance(t, BaseTool):
            out.append(t.name)
        elif isinstance(t, BaseToolSet):
            out.append(type(t).__name__)
        elif isinstance(t, type) and issubclass(t, BaseTool):
            out.append(t().name)
        elif callable(t):
            out.append(getattr(t, "__name__", repr(t)))
        else:
            out.append(type(t).__name__)
    return out


def render_archetype_block(archetype: SubAgentArchetype) -> str:
    tool_names = tool_names_of(archetype)
    tools_suffix = ", ".join(tool_names) if tool_names else "(none)"
    return f"- {archetype.name}: {archetype.description}\n  (Tools: {tools_suffix})"


def render_tool_description(registry: SubAgentRegistry) -> str:
    blocks = "\n".join(render_archetype_block(a) for a in registry.archetypes())
    return _HEADER + blocks + _FOOTER


__all__ = ["render_tool_description", "render_archetype_block", "tool_names_of"]
