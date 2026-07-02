# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""In-session create / transition logic for a single :class:`GoalRecord`.

All functions operate on an in-memory ``Optional[GoalRecord]``; persistence is
the caller's responsibility (tools write the serialised goal back through
``tool_context.state``). They enforce the 3-state contract:

  - at most **one** ``active`` goal per branch (``create`` rejects a duplicate);
  - terminal states (``complete`` / ``blocked``) are irreversible
    (``transition`` rejects when there is no active goal).
"""

from __future__ import annotations

import uuid
from typing import Optional
from typing import Tuple

from ._models import GoalRecord
from ._models import GoalStatus


def apply_create(
    existing: Optional[GoalRecord],
    *,
    objective: str,
    now_unix: int,
) -> Tuple[Optional[GoalRecord], Optional[str]]:
    """Create a new ``active`` goal.

    Returns ``(record, None)`` on success or ``(None, error)`` when an
    ``active`` goal already exists.
    """
    if existing is not None and existing.status == GoalStatus.ACTIVE:
        return None, "an active goal already exists; complete or block it before creating a new one"
    record = GoalRecord(
        id=uuid.uuid4().hex,
        objective=objective,
        status=GoalStatus.ACTIVE,
        created_at_unix=now_unix,
        updated_at_unix=now_unix,
    )
    return record, None


def apply_transition(
    existing: Optional[GoalRecord],
    *,
    status: GoalStatus,
    now_unix: int,
) -> Tuple[Optional[GoalRecord], Optional[str]]:
    """Move the active goal into a terminal state (``complete`` / ``blocked``).

    Returns ``(record, None)`` on success or ``(None, error)`` when there is no
    active goal, the goal is already terminal, or ``status`` is not terminal.
    """
    if status not in (GoalStatus.COMPLETE, GoalStatus.BLOCKED):
        return None, "status must be 'complete' or 'blocked'"
    if existing is None:
        return None, "no goal exists to update"
    if existing.status != GoalStatus.ACTIVE:
        return None, f"goal is already terminal (status={existing.status.value}) and cannot be changed"
    existing.status = status
    existing.updated_at_unix = now_unix
    existing.terminal_at_unix = now_unix
    return existing, None
