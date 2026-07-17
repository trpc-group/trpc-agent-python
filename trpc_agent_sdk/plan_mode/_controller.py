# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""PlanController — prompt injection, write gate, HITL resume handling."""

from __future__ import annotations

import inspect
import time
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import FrozenSet
from typing import List
from typing import Optional

from pydantic import BaseModel

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.tools._base_tool import BaseTool
from trpc_agent_sdk.types import FunctionResponse

from ._helpers import DEFAULT_FORCE_ENTER_PLAN_STATE_KEY
from ._helpers import DEFAULT_FORCE_ENTER_PLAN_STATE_VALUE
from ._helpers import DEFAULT_READONLY_SUBAGENT_TYPES
from ._helpers import DEFAULT_READONLY_TOOL_NAMES
from ._helpers import PLAN_TOOL_NAMES
from ._helpers import decode_plan
from ._helpers import encode_plan
from ._helpers import state_key
from ._long_running_tools import process_hitl_function_response
from ._models import PlanRecord
from ._models import PlanStatus
from ._prompt import _PLAN_AWARENESS_MARKER
from ._prompt import _PLAN_PROMPT_MARKER
from ._store import apply_enter

if TYPE_CHECKING:
    pass


class ApprovalEvent(BaseModel):
    """Observability payload when a plan is approved or rejected."""

    decision: str
    agent_name: str
    plan: PlanRecord


class _PlanCallbacks:
    """Model / tool callbacks for Plan Mode enforcement."""

    def __init__(
        self,
        *,
        state_key_prefix: str,
        plan_prompt: str,
        awareness_prompt: str,
        write_tool_names: FrozenSet[str],
        inject_prompt: bool,
        inject_awareness: bool,
        on_approval: Optional[Callable[[ApprovalEvent], None]],
        force_enter_plan_state_key: Optional[str] = DEFAULT_FORCE_ENTER_PLAN_STATE_KEY,
        force_enter_plan_state_value: str = DEFAULT_FORCE_ENTER_PLAN_STATE_VALUE,
        readonly_subagent_types: FrozenSet[str] = DEFAULT_READONLY_SUBAGENT_TYPES,
        readonly_tool_names: FrozenSet[str] = DEFAULT_READONLY_TOOL_NAMES,
    ) -> None:
        self._prefix = state_key_prefix
        self._plan_prompt = plan_prompt
        self._awareness_prompt = awareness_prompt
        self._write_tool_names = write_tool_names
        self._inject_prompt = inject_prompt
        self._inject_awareness = inject_awareness
        self._force_enter_plan_state_key = force_enter_plan_state_key
        self._force_enter_plan_state_value = force_enter_plan_state_value
        self._on_approval = on_approval
        self._readonly_subagent_types = readonly_subagent_types
        self._readonly_tool_names = readonly_tool_names

    def _resolve_branch(self, ctx: InvocationContext) -> str:
        return ctx.branch or ctx.agent_name or ""

    def _state_key(self, ctx: InvocationContext) -> str:
        return state_key(self._prefix, self._resolve_branch(ctx))

    def _load_plan(self, ctx: InvocationContext) -> Optional[PlanRecord]:
        return decode_plan(ctx.state.get(self._state_key(ctx)))

    def _save_plan(self, ctx: InvocationContext, plan: PlanRecord) -> None:
        branch = self._resolve_branch(ctx)
        if branch:
            plan.branch = branch
        ctx.state[self._state_key(ctx)] = encode_plan(plan)

    @staticmethod
    def _is_hitl_resume(ctx: InvocationContext) -> bool:
        if ctx.user_content is None or not ctx.user_content.parts:
            return False
        return any(part.function_response is not None for part in ctx.user_content.parts)

    @staticmethod
    def _objective_from_context(ctx: InvocationContext) -> str:
        if ctx.user_content and ctx.user_content.parts:
            texts = [part.text.strip() for part in ctx.user_content.parts if part.text and part.text.strip()]
            if texts:
                return "\n".join(texts)[:500]
        return "Current task"

    def _should_force_enter(self, ctx: InvocationContext) -> bool:
        """True when a session-state signal requests auto-enter."""
        if not self._force_enter_plan_state_key:
            return False
        return ctx.state.get(self._force_enter_plan_state_key) == self._force_enter_plan_state_value

    def _ensure_forced_plan(self, ctx: InvocationContext) -> None:
        """Auto-enter Plan Mode when the UI/session signal is active."""
        if not self._should_force_enter(ctx) or self._is_hitl_resume(ctx):
            return
        existing = self._load_plan(ctx)
        if existing is not None and existing.is_gate_active():
            return
        record, error = apply_enter(
            existing,
            objective=self._objective_from_context(ctx),
            now_unix=int(time.time()),
        )
        if error:
            logger.warning("auto-enter plan mode could not create plan: %s", error)
            return
        self._save_plan(ctx, record)

    @staticmethod
    def _latest_user_function_responses(request: LlmRequest) -> List[FunctionResponse]:
        """Function responses from the newest user turn only.

        ``before_model`` receives the full conversation, but HITL resume
        payloads must be applied at most once and only for the current turn.
        Re-playing an older ``exit_plan_mode`` rejection while a newer plan
        submission is ``pending_approval`` would roll status back to
        ``drafting`` and block implementation after a fresh approval.
        """
        for content in reversed(request.contents or []):
            if content.role != "user" or not content.parts:
                continue
            responses = [
                part.function_response for part in content.parts
                if part.function_response is not None and isinstance(part.function_response.response, dict)
            ]
            if responses:
                return responses
        return []

    def _process_hitl_in_request(self, ctx: InvocationContext, request: LlmRequest) -> None:
        for fr in self._latest_user_function_responses(request):
            result = process_hitl_function_response(
                ctx,
                name=fr.name,
                response=fr.response,
                state_key_prefix=self._prefix,
            )
            if result is not None and not result.get("error"):
                # Replace the host-supplied raw decision (e.g. just
                # {"status": "approved"}) with the state machine's
                # standardized payload (friendly message + full plan
                # dump) so the model sees the same response it would
                # get from a normal (non-HITL) tool call.
                fr.response = result
            if fr.name == "exit_plan_mode" and self._on_approval:
                plan = self._load_plan(ctx)
                if plan is not None:
                    try:
                        self._on_approval(
                            ApprovalEvent(
                                decision=str(fr.response.get("status", "")),
                                agent_name=ctx.agent_name,
                                plan=plan,
                            ))
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.warning("plan on_approval callback raised: %s", exc)

    def _existing_instructions(self, request: LlmRequest) -> str:
        if request.config and request.config.system_instruction:
            return str(request.config.system_instruction)
        return ""

    async def before_model(self, ctx: InvocationContext, request: LlmRequest) -> Optional[LlmResponse]:
        self._process_hitl_in_request(ctx, request)
        self._ensure_forced_plan(ctx)

        plan = self._load_plan(ctx)
        existing = self._existing_instructions(request)

        if plan is not None and plan.is_gate_active():
            if self._inject_prompt and _PLAN_PROMPT_MARKER not in existing:
                request.append_instructions([self._plan_prompt])
            return None

        if (not self._should_force_enter(ctx) and self._inject_awareness and _PLAN_AWARENESS_MARKER not in existing):
            request.append_instructions([self._awareness_prompt])
        return None

    async def before_tool(
        self,
        ctx: InvocationContext,
        tool: BaseTool,
        args: dict[str, Any],
        tool_ctx: dict,
    ) -> Optional[dict]:
        self._ensure_forced_plan(ctx)
        plan = self._load_plan(ctx)
        if plan is None or not plan.is_gate_active():
            return None

        tool_name = getattr(tool, "name", "") or ""

        if tool_name == "enter_plan_mode":
            if self._should_force_enter(ctx):
                return {
                    "error": ("PLAN_MODE_GATE: the user selected Plan Mode in the UI; entry is "
                              "automatic. Do not call enter_plan_mode — begin with spawn_subagent "
                              '("Explore") or ask_user_question.'),
                }
            if plan is not None and (plan.is_gate_active() or plan.status == PlanStatus.PENDING_ENTER):
                return {
                    "error": ("PLAN_MODE_GATE: already in Plan Mode "
                              f"(status={plan.status.value}). Do not call enter_plan_mode again — "
                              "continue planning with spawn_subagent, update_plan_content, or "
                              "exit_plan_mode."),
                }

        if tool_name in PLAN_TOOL_NAMES:
            return None

        if tool_name == "spawn_subagent":
            subagent_type = args.get("subagent_type")
            if subagent_type in self._readonly_subagent_types:
                return None
            return {
                "error": ("PLAN_MODE_GATE: spawn_subagent is restricted to read-only archetypes "
                          f"({', '.join(sorted(self._readonly_subagent_types))}) while plan status is "
                          f"{plan.status.value}. Other archetypes may inherit write-capable tools and "
                          "are blocked until the plan is approved."),
            }

        if tool_name == "dynamic_subagent":
            requested = args.get("tools")
            if isinstance(requested, list) and requested and all(name in self._readonly_tool_names
                                                                 for name in requested):
                return None
            return {
                "error": ("PLAN_MODE_GATE: dynamic_subagent is only allowed while plan status is "
                          f"{plan.status.value} if it explicitly restricts `tools` to a subset of "
                          f"{sorted(self._readonly_tool_names)}. Without an explicit restriction it "
                          "may inherit write-capable tools."),
            }

        if tool_name in self._write_tool_names:
            return {
                "error": (f"PLAN_MODE_GATE: tool `{tool_name}` is blocked while plan status is "
                          f"{plan.status.value}. Finish planning and get approval via exit_plan_mode, "
                          "or use read-only / spawn_subagent tools."),
            }
        return None


def _chain_callbacks(existing: Any, new: Callable) -> List[Callable]:
    if existing is None:
        return [new]
    if isinstance(existing, list):
        return [*existing, new]
    return [existing, new]


def _chain_tool_callback(existing: Any, new: Callable) -> Callable:

    async def _run_one(cb, ctx, tool, args, tool_ctx):
        result = cb(ctx, tool, args, tool_ctx)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def chained(ctx, tool, args, tool_ctx):
        if existing is not None:
            callbacks = existing if isinstance(existing, list) else [existing]
            for cb in callbacks:
                result = await _run_one(cb, ctx, tool, args, tool_ctx)
                if result is not None:
                    return result
        return await _run_one(new, ctx, tool, args, tool_ctx)

    return chained
