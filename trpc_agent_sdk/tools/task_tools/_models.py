# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Data models for the Task tool family.

These mirror Claude Code's structured Task tools (``TaskCreate`` /
``TaskUpdate`` / ``TaskGet`` / ``TaskList``). A task is identified by a
server-assigned ``id`` and updated incrementally, in contrast with the
whole-list-replacement model used by :mod:`trpc_agent_sdk.tools._todo_tool`.

The full board (:class:`TaskStore`) is serialised as a single JSON blob
into session-level state so it survives across ``Runner.run_async``
invocations and stays internally consistent on each read-modify-write.
:class:`_TaskToolBase` serialises access per session branch with an
``asyncio.Lock`` so parallel tool calls cannot corrupt ``highwatermark``
or ``tasks``.
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from pydantic import BaseModel
from pydantic import Field


class TaskStatus(str, Enum):
    """Lifecycle state of a single task."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    # Soft delete: kept in the store (so ids are never reused) but filtered
    # out of ``task_list`` by default.
    DELETED = "deleted"


class TaskRecord(BaseModel):
    """A single task entry.

    ``active_form`` / ``blocked_by`` are exposed to the model and persisted
    under the camelCase aliases ``activeForm`` / ``blockedBy`` to stay
    compatible with Claude Code's schema.
    """

    id: str = Field(description="Server-assigned, monotonically increasing id.")
    subject: str = Field(description="Short imperative title, e.g. 'Run tests'.")
    description: str = Field(default="", description="Free-form details.")
    active_form: Optional[str] = Field(
        default=None,
        alias="activeForm",
        description="Present-continuous form shown while in_progress.",
    )
    owner: Optional[str] = Field(default=None, description="Claiming agent / worker id.")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="Lifecycle state.")
    blocks: List[str] = Field(default_factory=list, description="Downstream task ids this task blocks.")
    blocked_by: List[str] = Field(
        default_factory=list,
        alias="blockedBy",
        description="Upstream task ids that block this task.",
    )
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Arbitrary extension data.")

    model_config = {"populate_by_name": True}


class TaskStore(BaseModel):
    """The complete in-session task board, serialised into session state."""

    highwatermark: int = Field(default=0, description="Highest id ever allocated; ids are never reused.")
    tasks: Dict[str, TaskRecord] = Field(default_factory=dict, description="Task id -> record.")

    model_config = {"populate_by_name": True}


class TaskListSummary(BaseModel):
    """Token-optimised summary returned by ``task_list`` (no ``description``)."""

    id: str
    subject: str
    status: TaskStatus
    owner: Optional[str] = None
    active_form: Optional[str] = Field(default=None, alias="activeForm")
    blocked_by: List[str] = Field(default_factory=list, alias="blockedBy")

    model_config = {"populate_by_name": True}
