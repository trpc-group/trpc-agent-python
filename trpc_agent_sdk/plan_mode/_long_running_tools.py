# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Long-running Plan Mode tools (HITL): enter_plan_mode, exit_plan_mode, ask_user_question."""

from __future__ import annotations

import time
import uuid
from typing import Any
from typing import List
from typing import Optional

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.tools._long_running_tool import LongRunningFunctionTool

from ._helpers import DEFAULT_STATE_KEY_PREFIX
from ._helpers import decode_plan
from ._helpers import encode_plan
from ._helpers import state_key
from ._lock import plan_store_lock
from ._lock import release_lock
from ._models import PlanStatus
from ._prompt import DEFAULT_ASK_DESCRIPTION
from ._prompt import DEFAULT_ENTER_DESCRIPTION
from ._prompt import DEFAULT_EXIT_DESCRIPTION
from ._store import apply_enter_decision
from ._store import apply_question_answer
from ._store import apply_register_question
from ._store import apply_request_enter
from ._store import apply_request_exit


def _resolve_branch(tool_context: InvocationContext) -> str:
    return tool_context.branch or tool_context.agent_name or ""


def _load(tool_context: InvocationContext, prefix: str):
    return decode_plan(tool_context.state.get(state_key(prefix, _resolve_branch(tool_context))))


def _save(tool_context: InvocationContext, prefix: str, record) -> None:
    tool_context.state[state_key(prefix, _resolve_branch(tool_context))] = encode_plan(record)


def _clear(tool_context: InvocationContext, prefix: str) -> None:
    tool_context.state[state_key(prefix, _resolve_branch(tool_context))] = None


def make_enter_plan_mode_tool(
    *,
    state_key_prefix: str = DEFAULT_STATE_KEY_PREFIX,
) -> LongRunningFunctionTool:
    """Build enter_plan_mode LongRunningFunctionTool bound to ``state_key_prefix``."""

    async def enter_plan_mode(
            objective: str,
            tool_context: InvocationContext = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        """Request human confirmation before entering Plan Mode."""
        if not isinstance(objective, str) or not objective.strip():
            return {"error": "INVALID_ARGS: `objective` is required and must be a non-empty string"}

        branch = _resolve_branch(tool_context)
        async with plan_store_lock(tool_context, prefix=state_key_prefix, branch=branch):
            request_id = uuid.uuid4().hex
            record, error, payload = apply_request_enter(
                _load(tool_context, state_key_prefix),
                objective=objective.strip(),
                request_id=request_id,
                now_unix=int(time.time()),
            )
            if error:
                return {"error": f"INVALID_STATE: {error}"}
            _save(tool_context, state_key_prefix, record)
            return payload

    enter_plan_mode.__name__ = "enter_plan_mode"
    enter_plan_mode.__doc__ = DEFAULT_ENTER_DESCRIPTION
    return LongRunningFunctionTool(enter_plan_mode)


def make_exit_plan_mode_tool(
    *,
    state_key_prefix: str = DEFAULT_STATE_KEY_PREFIX,
) -> LongRunningFunctionTool:
    """Build exit_plan_mode LongRunningFunctionTool bound to ``state_key_prefix``."""

    async def exit_plan_mode(
            summary: str = "",
            tool_context: InvocationContext = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        """Submit the plan for human approval."""
        branch = _resolve_branch(tool_context)
        async with plan_store_lock(tool_context, prefix=state_key_prefix, branch=branch):
            request_id = uuid.uuid4().hex
            record, error, payload = apply_request_exit(
                _load(tool_context, state_key_prefix),
                summary=summary or "",
                request_id=request_id,
                now_unix=int(time.time()),
            )
            if error:
                return {"error": f"INVALID_STATE: {error}"}
            _save(tool_context, state_key_prefix, record)
            return payload

    exit_plan_mode.__name__ = "exit_plan_mode"
    exit_plan_mode.__doc__ = DEFAULT_EXIT_DESCRIPTION
    return LongRunningFunctionTool(exit_plan_mode)


def make_ask_user_question_tool(
    *,
    state_key_prefix: str = DEFAULT_STATE_KEY_PREFIX,
) -> LongRunningFunctionTool:
    """Build ask_user_question LongRunningFunctionTool."""

    async def ask_user_question(
            question: str,
            options: Optional[List[str]] = None,
            tool_context: InvocationContext = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        """Ask the user a structured question during Plan Mode."""
        if not isinstance(question, str) or not question.strip():
            return {"error": "INVALID_ARGS: `question` is required"}
        branch = _resolve_branch(tool_context)
        async with plan_store_lock(tool_context, prefix=state_key_prefix, branch=branch):
            request_id = uuid.uuid4().hex
            record, error, payload = apply_register_question(
                _load(tool_context, state_key_prefix),
                question=question.strip(),
                options=options,
                request_id=request_id,
                now_unix=int(time.time()),
            )
            if error:
                return {"error": f"INVALID_STATE: {error}"}
            _save(tool_context, state_key_prefix, record)
            return payload

    ask_user_question.__name__ = "ask_user_question"
    ask_user_question.__doc__ = DEFAULT_ASK_DESCRIPTION
    return LongRunningFunctionTool(ask_user_question)


def process_hitl_function_response(
    tool_context: InvocationContext,
    *,
    name: str,
    response: dict[str, Any],
    state_key_prefix: str = DEFAULT_STATE_KEY_PREFIX,
) -> Optional[dict[str, Any]]:
    """Apply human resume payload for enter_plan_mode / exit_plan_mode / ask_user_question.

    Returns the synthetic tool result dict to inject, or None if not handled.
    """
    if not isinstance(response, dict):
        return None

    branch = _resolve_branch(tool_context)
    key = state_key(state_key_prefix, branch)

    if name == "enter_plan_mode":
        status = response.get("status")
        if status == "pending_enter":
            return None
        if status not in ("approved", "rejected"):
            return None
        if isinstance(response.get("plan"), dict):
            return None

        existing = _load(tool_context, state_key_prefix)
        if existing is not None and existing.status != PlanStatus.PENDING_ENTER:
            logger.debug(
                "Ignoring enter_plan_mode HITL %s while plan status is %s",
                status,
                existing.status.value if existing is not None else "none",
            )
            return None

        record, error, result = apply_enter_decision(
            _load(tool_context, state_key_prefix),
            decision=status,
            reviewer_note=str(response.get("reviewer_note") or response.get("message") or ""),
            now_unix=int(time.time()),
        )
        if error:
            return {"error": error}
        if record is None:
            tool_context.state[key] = None
        else:
            tool_context.state[key] = encode_plan(record)
        return result

    if name == "exit_plan_mode":
        status = response.get("status")
        if status == "pending_approval":
            return None
        if status not in ("approved", "rejected"):
            return None
        # Already-normalized tool results from a prior resume must not be
        # re-applied when history is scanned again on later turns.
        if isinstance(response.get("plan"), dict):
            return None
        from ._store import apply_approval_decision

        existing = _load(tool_context, state_key_prefix)
        if existing is not None and existing.status != PlanStatus.PENDING_APPROVAL:
            logger.debug(
                "Ignoring exit_plan_mode HITL %s while plan status is %s",
                status,
                existing.status.value,
            )
            return None

        record, error, result = apply_approval_decision(
            _load(tool_context, state_key_prefix),
            decision=status,
            reviewer_note=str(response.get("reviewer_note") or response.get("message") or ""),
            edited_content=response.get("content") if isinstance(response.get("content"), str) else None,
            now_unix=int(time.time()),
        )
        if error:
            return {"error": error}
        tool_context.state[key] = encode_plan(record)
        if record.status == PlanStatus.APPROVED:
            release_lock(tool_context, prefix=state_key_prefix, branch=branch)
        return result

    if name == "ask_user_question":
        status = response.get("status")
        if status in ("pending_question", "answered"):
            return None
        answer = response.get("answer")
        if not isinstance(answer, str):
            return None
        qid = response.get("question_id")
        if not isinstance(qid, int):
            return None
        record, error, result = apply_question_answer(
            _load(tool_context, state_key_prefix),
            question_id=qid,
            answer=answer,
        )
        if error:
            return {"error": error}
        tool_context.state[key] = encode_plan(record)
        return result

    return None
