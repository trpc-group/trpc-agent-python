# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""DynamicAgentTool — on-the-fly sub-agent creation where the LLM defines the role."""

from __future__ import annotations

from typing import Any
from typing import List
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ._archetype import SubAgentArchetype
from ._runner import run_subagent
from ._sub_agent_config import SubAgentConfig

_DESCRIPTION = ("Run one short-lived sub-agent for a single focused task and return "
                "its result. The sub-agent is created on the fly for this call only "
                "and is destroyed afterward. It does NOT transfer control, does NOT "
                "run a pre-registered agent by name, and does NOT start a background "
                "task. To run several tasks, call this tool multiple times. Its tools "
                "stay within a code-defined capability boundary, which by default is "
                "derived from what the current agent is already allowed to use (or "
                "set explicitly in code), and it cannot select arbitrary agents, "
                "models, or executors. IMPORTANT: The sub-agent cannot spawn further "
                "sub-agents.")

_FALLBACK_INSTRUCTION = """\
You are a focused sub-agent. Use the tools available to complete the task \
described in the prompt. Be thorough but concise; return a single result \
that the parent agent can act on directly."""


class DynamicAgentTool(BaseTool):
    """Run a short-lived sub-agent whose role is defined at call time via
    ``instruction``. By default the sub-agent inherits the parent agent's
    full tool surface; pass ``tools`` to use a fixed tool set.

    Use this when you cannot predict all the specialist types you'll need
    ahead of time — the LLM invents the right role for each task.

    Args:
        name: Name of the tool as seen by the LLM. Defaults to
            ``"dynamic_agent"``.
        description: Tool description as seen by the LLM. Defaults to
            a pre-built description.
        tools: Tools available to the sub-agent. ``None`` (default) means
            inherit all parent tools. Pass a tuple of ``BaseTool`` instances
            or factory callables to use a fixed tool set instead.
        expose_tool_selection: When ``True`` (default), the ``tools`` field is
            exposed in the schema so the model can restrict which tools the
            sub-agent may use. When ``False``, the model cannot narrow the tool
            surface.
        agent_config: :class:`SubAgentConfig` applied to every spawned
            sub-agent. Only non-``None`` fields are forwarded to the
            ``LlmAgent`` constructor.
        skip_summarization: When ``True``, the parent agent's LLM loop exits
            immediately after the sub-agent returns.
        filters_name: Filter instance names forwarded to :class:`BaseTool`.
        filters: Filter instances forwarded to :class:`BaseTool`.
    """

    def __init__(
        self,
        name: str = "dynamic_agent",
        description: Optional[str] = None,
        tools: Optional[tuple] = None,
        expose_tool_selection: bool = True,
        agent_config: Optional[SubAgentConfig] = None,
        skip_summarization: bool = False,
        filters_name: Optional[List[str]] = None,
        filters: Optional[List[BaseFilter]] = None,
    ) -> None:
        self._tools = tools
        self._agent_config = agent_config
        self._skip_summarization = skip_summarization
        self._expose_tool_selection = expose_tool_selection
        super().__init__(name=name, description=description or _DESCRIPTION, filters_name=filters_name, filters=filters)

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        properties: dict = {
            "prompt":
            Schema(
                type=Type.STRING,
                description=("The task for the sub-agent. Include all the "
                             "context it needs to complete the task on its own."),
            ),
            "instruction":
            Schema(
                type=Type.STRING,
                description=("Optional role, constraints, and execution guidance "
                             "for this sub-agent invocation. It acts as the "
                             "sub-agent's system prompt for this run."),
            ),
        }

        if self._expose_tool_selection:
            tools_desc = "Optional exact tool names this sub-agent may use. " \
                         "Omit to allow all permitted tools."
            if self._tools is not None:
                names = _tool_names(self._tools)
                if names:
                    tools_desc += " Available tool names: " + ", ".join(names) + "."
            properties["tools"] = Schema(
                type=Type.ARRAY,
                description=tools_desc,
                items=Schema(type=Type.STRING),
            )

        return FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=Schema(
                type=Type.OBJECT,
                properties=properties,
                required=["prompt"],
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
            instruction = (f"When using `{self.name}`: The sub-agent can see the "
                           "current conversation's history. Use it when delegated "
                           "tool work should run in a child invocation while "
                           "continuing from the current conversation. Describe the "
                           "task in `prompt`, and optionally set `instruction` to "
                           "give the sub-agent a role or constraints.")
        else:
            instruction = (f"When using `{self.name}`: The sub-agent has no memory "
                           "of this conversation. Use it for self-contained tool "
                           "work or any task where delegating keeps the parent "
                           "conversation focused. Put everything it needs in `prompt`. "
                           "Optionally set `instruction` to give the sub-agent a role "
                           "or constraints for this run.")
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

        instruction = args.get("instruction")
        prompt = args.get("prompt")

        if not isinstance(instruction, str) or not instruction.strip():
            instruction = _FALLBACK_INSTRUCTION
        if not isinstance(prompt, str) or not prompt.strip():
            return {"status": "error", "message": "prompt must be a non-empty string"}

        # Resolve tools.
        # self._tools: user-configured capability ceiling.
        #   None  → inherit parent tools
        #   tuple → fixed tool set
        # tools_arg (LLM call-time): optional name-based narrowing, only
        #   honored when expose_tool_selection is True.
        #   not provided → no filter
        #   list of names → filter by name (handled in _build_sub_agent)
        tool_filter = None
        if self._expose_tool_selection:
            tools_arg = args.get("tools")
            tool_filter = tools_arg if isinstance(tools_arg, list) else None

        synthetic = SubAgentArchetype(
            name="dynamic",
            description="A focused sub-agent created dynamically for a specific task.",
            instruction=instruction,
            tools=self._tools,  # None=inherit, tuple=fixed set
        )
        return await run_subagent(
            parent_ctx=tool_context,
            archetype=synthetic,
            prompt=prompt,
            agent_config=self._agent_config,
            tool_filter=tool_filter,
        )


def _tool_names(tools: tuple) -> list[str]:
    """Extract declaration names from a tuple of tool items.

    Handles ``BaseTool`` instances, ``BaseToolSet`` instances, and factory
    callables (e.g. class references).
    """
    from trpc_agent_sdk.tools import BaseToolSet

    names: list[str] = []
    for t in tools:
        if isinstance(t, BaseTool):
            name = getattr(t, 'name', None)
        elif isinstance(t, BaseToolSet):
            name = type(t).__name__
        elif isinstance(t, type) and issubclass(t, BaseTool):
            name = t().name
        elif callable(t):
            name = getattr(t, "__name__", None)
        else:
            continue
        if name:
            names.append(name)
    return names


__all__ = ["DynamicAgentTool"]
