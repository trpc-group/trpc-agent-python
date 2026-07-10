# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""SpawnSubAgentTool — spawn sub-agents from pre-registered archetype templates."""

from __future__ import annotations

import os
from typing import Any
from typing import List
from typing import Optional
from typing import Union
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ._archetype import SubAgentArchetype
from ._defaults import DEFAULT_AGENT
from ._description import render_tool_description
from ._loader import load_archetypes_from_dir
from ._registry import SubAgentRegistry
from ._runner import run_subagent
from ._sub_agent_config import SubAgentConfig


class SpawnSubAgentTool(BaseTool):
    """Tool for spawning pre-defined archetype-based sub-agents.

    Each archetype has a locked instruction, tool set, and model — the LLM
    selects which archetype to use and writes the task prompt, but cannot
    redefine the archetype's role or capabilities at call time.

    Pre-built archetypes (``DEFAULT_AGENT``, ``GENERAL_PURPOSE_AGENT``,
    ``EXPLORE_AGENT``, ``PLAN_AGENT``) are exported from the package for
    manual composition; only ``default`` is auto-registered.

    Archetypes can be loaded from ``*.md`` files::

        ---
        name: my-researcher
        description: Use this agent for deep research tasks.
        tools:            # optional; if omitted, sub-agent inherits parent tools
          - Read
          - websearch
        ---

        You are a research specialist.  Your task is to …

    Args:
        agents: Additional archetypes to register (or override ``default``
            with a custom version).
        agent_paths: One or more directories of ``*.md`` files to load
            archetypes from disk.
        tool_mapping: Optional name-to-class mapping for resolving custom
            tool names in MD frontmatter (``agent_paths``). Merged with the
            built-in whitelist; custom entries take precedence.
        with_default: Whether to register the built-in ``default``
            archetype as a universal fallback. Defaults to ``True``;
            set to ``False`` when you want full control over the archetype catalog.
        agent_config: :class:`SubAgentConfig` applied to every spawned
            sub-agent. Only non-``None`` fields are forwarded to the
            ``LlmAgent`` constructor.
        skip_summarization: When ``True``, the parent agent's LLM loop exits
            immediately after the sub-agent returns, saving the token cost of
            a final summarization turn.
        filters_name: Filter instance names forwarded to :class:`BaseTool`.
        filters: Filter instances forwarded to :class:`BaseTool`.
    """

    def __init__(
        self,
        agents: Optional[List[SubAgentArchetype]] = None,
        agent_paths: Optional[List[Union[str, os.PathLike]]] = None,
        tool_mapping: Optional[dict[str, Any]] = None,
        with_default: bool = True,
        agent_config: Optional[SubAgentConfig] = None,
        skip_summarization: bool = False,
        filters_name: Optional[List[str]] = None,
        filters: Optional[List[BaseFilter]] = None,
    ) -> None:
        registry = SubAgentRegistry()
        if with_default:
            registry.register(DEFAULT_AGENT)
        for archetype in agents or []:
            if archetype.name in registry:
                raise ValueError(f"archetype name {archetype.name!r} collides with an "
                                 "already-registered archetype")
            registry.register(archetype)
        if agent_paths is not None:
            for path in agent_paths:
                for archetype in load_archetypes_from_dir(path, tool_mapping=tool_mapping):
                    if archetype.name in registry:
                        raise ValueError(f"archetype name {archetype.name!r} from {path!r} "
                                         "collides with an already-registered archetype")
                    registry.register(archetype)

        self._registry = registry
        self._skip_summarization = skip_summarization
        self._agent_config = agent_config
        rendered = render_tool_description(registry)
        super().__init__(name="spawn_subagent", description=rendered, filters_name=filters_name, filters=filters)

    @property
    def registry(self) -> SubAgentRegistry:
        return self._registry

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "subagent_type":
                    Schema(
                        type=Type.STRING,
                        enum=self._registry.names(),
                        description=("The type of specialized agent to use for this task. "
                                     "See tool description for capabilities of each."),
                    ),
                    "prompt":
                    Schema(
                        type=Type.STRING,
                        description=("The task for the sub-agent. Include all the "
                                     "context it needs to complete the task on its own."),
                    ),
                    "description":
                    Schema(
                        type=Type.STRING,
                        description=("Short label (3-7 words) of what this sub-agent will do."),
                    ),
                },
                required=["prompt", "description"],
            ),
            response=Schema(type=Type.STRING),
        )

    @override
    async def process_request(
        self,
        *,
        tool_context: InvocationContext,
        llm_request: LlmRequest,
    ) -> None:
        await super().process_request(tool_context=tool_context, llm_request=llm_request)
        include_parent_history = (self._agent_config is not None and self._agent_config.include_parent_history)
        if include_parent_history:
            instruction = ("When using `spawn_subagent`: The sub-agent can see the "
                           "current conversation's history. Use it when delegated "
                           "tool work should run in a child invocation while "
                           "continuing from the current conversation. Still describe "
                           "the task in `prompt`.")
        else:
            instruction = ("When using `spawn_subagent`: The sub-agent has no memory "
                           "of this conversation. Use it for self-contained tool "
                           "work, multiple independent subtasks, or any task where "
                           "delegating keeps the parent conversation focused instead "
                           "of filling it with tool details and intermediate steps. "
                           "Put everything it needs in `prompt`.")
        llm_request.append_instructions([instruction])

    @override
    async def _run_async_impl(
        self,
        *,
        tool_context: InvocationContext,
        args: dict[str, Any],
    ) -> Any:
        if self._skip_summarization:
            tool_context.event_actions.skip_summarization = True

        subagent_type = args.get("subagent_type")
        prompt = args.get("prompt")

        # Resolve subagent_type, falling back to default if missing or unknown.
        if isinstance(subagent_type, str) and subagent_type in self._registry:
            resolved_type = subagent_type
        elif "default" in self._registry:
            resolved_type = "default"
        else:
            return {
                "status": "error",
                "message": (f"unknown subagent_type: {subagent_type!r}. "
                            f"Available: {self._registry.names()}"),
            }
        if not isinstance(prompt, str) or not prompt.strip():
            return {"status": "error", "message": "prompt must be a non-empty string"}

        archetype = self._registry.get(resolved_type)
        return await run_subagent(
            parent_ctx=tool_context,
            archetype=archetype,
            prompt=prompt,
            agent_config=self._agent_config,
        )


__all__ = ["SpawnSubAgentTool"]
