# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Human-in-the-loop tools layered on top of TodoWriteTool."""

from __future__ import annotations

import time
import uuid
from typing import Any

from pydantic import ValidationError

from trpc_agent_sdk.tools import TodoItem
from trpc_agent_sdk.tools import render_todos
from trpc_agent_sdk.tools import validate_todos


async def request_todo_plan_approval(todos: list[dict[str, Any]], summary: str = "") -> dict:
    """Request human approval before persisting a new todo plan.

    The agent should call this tool **before** the first ``todo_write`` when
    laying out a multi-step plan. After the human approves (or edits) the
    plan, call ``todo_write`` with the approved list. Subsequent status
    updates can go directly to ``todo_write`` without another approval.

    Args:
        todos: Proposed complete todo list (same shape as ``todo_write``).
        summary: Short rationale for the plan shown to the reviewer.

    Returns:
        A pending-approval payload consumed by ``LongRunningEvent`` handling.
    """
    if not isinstance(todos, list):
        return {"error": "INVALID_ARGS: `todos` must be an array"}

    try:
        items = [TodoItem.model_validate(x) for x in todos]
    except (ValidationError, TypeError) as exc:
        return {"error": f"INVALID_ARGS: each todo must have content/activeForm/status: {exc}"}

    if err := validate_todos(items):
        return {"error": f"INVALID_TODOS: {err}"}

    return {
        "status": "pending_approval",
        "message": summary or "New todo plan requires human approval before persisting.",
        "todos": [t.model_dump(mode="json", by_alias=True) for t in items],
        "preview": render_todos(items),
        "approval_id": str(uuid.uuid4()),
        "timestamp": time.time(),
    }
