# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""State-key handling and serialisation for Plan Mode."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from typing import Any
from typing import List
from typing import Optional

from trpc_agent_sdk.log import logger

from ._models import PlanRecord
from ._models import PlanStatus

if TYPE_CHECKING:
    from trpc_agent_sdk.context import InvocationContext

DEFAULT_STATE_KEY_PREFIX = "plan"

# Session-state key/value pair used by AG-UI (or other hosts) to signal that the
# user selected Plan Mode in the UI. When matched, ``_PlanCallbacks`` auto-enters
# plan mode without waiting for ``enter_plan_mode``.
DEFAULT_FORCE_ENTER_PLAN_STATE_KEY = "agent_mode"
DEFAULT_FORCE_ENTER_PLAN_STATE_VALUE = "plan"

# Write-tool denylist for Plan Mode gate (``_PlanCallbacks.before_tool``).
#
# Matched by exact ``tool.name`` string — a typo or stale name silently
# skips blocking. Extend at mount time via ``PlanOptions.write_tool_names``.
#
# Default entries map to built-in SDK toolsets:
#   file_tools  → Write, Edit, Bash
#   _todo_tool  → todo_write
#   task_tools  → task_create, task_update
#   goal_tools  → create_goal, update_goal
#
# Not covered here: spawn_subagent / dynamic_subagent (separate gate rules
# in ``_controller.py``). Skills, OpenClaw, MCP, and custom tools must be
# added explicitly if the agent mounts them.
DEFAULT_WRITE_TOOL_NAMES = frozenset({
    "Write",
    "Edit",
    "Bash",
    "todo_write",
    "task_create",
    "task_update",
    "create_goal",
    "update_goal",
})

# Sub-agent archetypes considered safe to spawn while the plan gate is
# active (their tool surface is fixed to read-only tools — see
# trpc_agent_sdk.agents.sub_agent._defaults.EXPLORE_AGENT / PLAN_AGENT).
# spawn_subagent calls that request any other subagent_type (e.g. "default",
# which inherits the parent's full — potentially write-capable — tool
# surface) are treated as regular tool calls and subject to the write gate.
DEFAULT_READONLY_SUBAGENT_TYPES = frozenset({"Explore", "Plan"})

# Tool names dynamic_subagent is allowed to narrow itself to while the plan
# gate is active. A dynamic_subagent call is only let through the gate if it
# explicitly restricts itself (via its ``tools`` argument) to names from this
# set; otherwise it could inherit the parent's full tool surface and is
# blocked like any other write-capable call.
DEFAULT_READONLY_TOOL_NAMES = frozenset({"Read", "Grep", "Glob", "webfetch", "websearch"})

PLAN_TOOL_NAMES = frozenset({
    "enter_plan_mode",
    "update_plan_content",
    "exit_plan_mode",
    "ask_user_question",
})


def should_hide_enter_plan_mode_tool(
    invocation_context: Optional["InvocationContext"],
    *,
    state_key_prefix: str = DEFAULT_STATE_KEY_PREFIX,
    force_enter_plan_state_key: Optional[str] = DEFAULT_FORCE_ENTER_PLAN_STATE_KEY,
    force_enter_plan_state_value: str = DEFAULT_FORCE_ENTER_PLAN_STATE_VALUE,
) -> bool:
    """True when ``enter_plan_mode`` should be omitted from the LLM tool schema.

    Hosts (e.g. AG-UI Plan toggle) set ``agent_mode=plan`` in session state to
    auto-enter; exposing ``enter_plan_mode`` would invite redundant HITL calls.
    Also hides the tool while a plan gate is already active.
    """
    if invocation_context is None:
        return False
    state = invocation_context.state
    if (force_enter_plan_state_key and state.get(force_enter_plan_state_key) == force_enter_plan_state_value):
        return True
    branch = invocation_context.branch or invocation_context.agent_name or ""
    plan = decode_plan(state.get(state_key(state_key_prefix, branch)))
    if plan is None:
        return False
    return plan.is_gate_active() or plan.status == PlanStatus.PENDING_ENTER


def state_key(prefix: str, branch: str) -> str:
    """Build the state key, appending ``:<branch>`` for multi-branch isolation."""
    prefix = prefix or DEFAULT_STATE_KEY_PREFIX
    return prefix if not branch else f"{prefix}:{branch}"


def decode_plan(raw: Any) -> Optional[PlanRecord]:
    """Decode persisted plan JSON; malformed data degrades to ``None``."""
    if not raw:
        return None
    try:
        if isinstance(raw, str):
            return PlanRecord.model_validate_json(raw)
        if isinstance(raw, dict):
            return PlanRecord.model_validate(raw)
    except (ValueError, TypeError) as exc:
        logger.warning("Plan mode failed to decode persisted plan: %s", exc)
    return None


def encode_plan(plan: PlanRecord) -> str:
    """Serialise a plan to JSON (camelCase aliases)."""
    return plan.model_dump_json(by_alias=True)


def get_plan_record(
    session: Any,
    branch: str = "",
    prefix: str = DEFAULT_STATE_KEY_PREFIX,
) -> Optional[PlanRecord]:
    """Read the current plan from a session object with a ``state`` mapping."""
    state = getattr(session, "state", None) or {}
    return decode_plan(state.get(state_key(prefix, branch)))


def render_plan(plan: Optional[PlanRecord]) -> str:
    """Compact ASCII card for logs / CLIs."""
    if plan is None:
        return "(no plan)"
    lines = [
        f"📋 Plan [{plan.status.value}]",
        f"   objective: {plan.objective}",
        f"   revisions: {plan.content_revisions}",
    ]
    if plan.content:
        preview = plan.content.strip().splitlines()
        snippet = "\n".join(f"   | {line}" for line in preview[:8])
        if len(preview) > 8:
            snippet += f"\n   | ... ({len(preview) - 8} more lines)"
        lines.append("   content:")
        lines.append(snippet)
    return "\n".join(lines)


def plan_to_task_subjects(plan: PlanRecord) -> List[str]:
    """Heuristic: Markdown ``##`` headings → task subject lines (helper for post-approval)."""
    subjects: List[str] = []
    for line in plan.content.splitlines():
        match = re.match(r"^##\s+(.+)$", line.strip())
        if match:
            subjects.append(match.group(1).strip())
    return subjects
