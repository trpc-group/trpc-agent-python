# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Data model for the Goal (persistent session objective) capability.

A goal is a single, session-scoped contract that survives across
``Runner.run_async`` invocations: while it is ``active`` a "looks final"
model response does **not** mean the task is done — the model must either keep
working or explicitly mark the goal ``complete`` / ``blocked`` via
``update_goal``.

Unlike :mod:`trpc_agent_sdk.tools.task_tools` (a multi-item board) there is at
most **one** goal per session branch, serialised as a single JSON blob into
session-level state. Serialisation mirrors the task tools: camelCase aliases
plus ``model_dump_json(by_alias=True)``.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel
from pydantic import Field


class GoalStatus(str, Enum):
    """Lifecycle state of a session goal (3-state, terminal states irreversible)."""

    ACTIVE = "active"
    """Still needs to be pursued."""

    BLOCKED = "blocked"
    """Progress depends on external input / state change (terminal)."""

    COMPLETE = "complete"
    """The objective has genuinely been met (terminal)."""


class GoalRecord(BaseModel):
    """A single session goal.

    ``created_at_unix`` / ``updated_at_unix`` / ``terminal_at_unix`` are
    persisted under the camelCase aliases ``createdAtUnix`` / ``updatedAtUnix``
    / ``terminalAtUnix``.
    """

    id: str = Field(description="Server-assigned unique id (uuid).")
    objective: str = Field(description="Completion criteria — the contract text.")
    status: GoalStatus = Field(default=GoalStatus.ACTIVE, description="Lifecycle state.")
    created_at_unix: int = Field(alias="createdAtUnix", description="Creation time (unix seconds).")
    updated_at_unix: int = Field(alias="updatedAtUnix", description="Last update time (unix seconds).")
    terminal_at_unix: Optional[int] = Field(
        default=None,
        alias="terminalAtUnix",
        description="Time the goal entered a terminal state (unix seconds).",
    )

    model_config = {"populate_by_name": True}
