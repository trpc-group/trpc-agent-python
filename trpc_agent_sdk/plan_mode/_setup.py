# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""``setup_plan`` — mount Plan Mode on an :class:`LlmAgent`."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Callable
from typing import FrozenSet
from typing import Optional

from ._controller import ApprovalEvent
from ._controller import _PlanCallbacks
from ._controller import _chain_callbacks
from ._controller import _chain_tool_callback
from ._helpers import DEFAULT_FORCE_ENTER_PLAN_STATE_KEY
from ._helpers import DEFAULT_FORCE_ENTER_PLAN_STATE_VALUE
from ._helpers import DEFAULT_READONLY_SUBAGENT_TYPES
from ._helpers import DEFAULT_READONLY_TOOL_NAMES
from ._helpers import DEFAULT_STATE_KEY_PREFIX
from ._helpers import DEFAULT_WRITE_TOOL_NAMES
from ._plan_toolset import PlanToolSet
from ._prompt import DEFAULT_PLAN_AWARENESS_PROMPT
from ._prompt import DEFAULT_PLAN_MODE_PROMPT

if TYPE_CHECKING:
    from trpc_agent_sdk.agents import LlmAgent

OnApproval = Callable[[ApprovalEvent], None]


@dataclass
class PlanOptions:
    """Configuration for Plan Mode."""

    state_key_prefix: str = DEFAULT_STATE_KEY_PREFIX
    plan_prompt: str = DEFAULT_PLAN_MODE_PROMPT
    awareness_prompt: str = DEFAULT_PLAN_AWARENESS_PROMPT
    write_tool_names: FrozenSet[str] = field(default_factory=lambda: DEFAULT_WRITE_TOOL_NAMES)
    inject_prompt: bool = True
    inject_awareness: bool = True
    force_enter_plan_state_key: Optional[str] = DEFAULT_FORCE_ENTER_PLAN_STATE_KEY
    """Session-state key checked per invocation; when its value equals
    ``force_enter_plan_state_value``, auto-enter plan mode without ``enter_plan_mode``.
    Set to ``None`` to disable UI-driven auto-enter."""
    force_enter_plan_state_value: str = DEFAULT_FORCE_ENTER_PLAN_STATE_VALUE
    on_approval: Optional[OnApproval] = None
    readonly_subagent_types: FrozenSet[str] = field(default_factory=lambda: DEFAULT_READONLY_SUBAGENT_TYPES)
    """spawn_subagent archetypes let through the write gate (must be read-only by tool surface)."""
    readonly_tool_names: FrozenSet[str] = field(default_factory=lambda: DEFAULT_READONLY_TOOL_NAMES)
    """Tool names dynamic_subagent may self-restrict to in order to pass the write gate."""

    def toolset(self) -> PlanToolSet:
        return PlanToolSet(
            state_key_prefix=self.state_key_prefix,
            force_enter_plan_state_key=self.force_enter_plan_state_key,
            force_enter_plan_state_value=self.force_enter_plan_state_value,
        )


def setup_plan(agent: "LlmAgent", opts: Optional[PlanOptions] = None) -> "LlmAgent":
    """Mount Plan Mode: tools + prompt injection + write gate + HITL resume.

    Returns the same ``agent`` for chaining.
    """
    opts = opts or PlanOptions()
    callbacks = _PlanCallbacks(
        state_key_prefix=opts.state_key_prefix,
        plan_prompt=opts.plan_prompt,
        awareness_prompt=opts.awareness_prompt,
        write_tool_names=opts.write_tool_names,
        inject_prompt=opts.inject_prompt,
        inject_awareness=opts.inject_awareness,
        force_enter_plan_state_key=opts.force_enter_plan_state_key,
        force_enter_plan_state_value=opts.force_enter_plan_state_value,
        on_approval=opts.on_approval,
        readonly_subagent_types=opts.readonly_subagent_types,
        readonly_tool_names=opts.readonly_tool_names,
    )
    agent.tools.append(opts.toolset())
    agent.before_model_callback = _chain_callbacks(agent.before_model_callback, callbacks.before_model)
    agent.before_tool_callback = _chain_tool_callback(agent.before_tool_callback, callbacks.before_tool)
    return agent
