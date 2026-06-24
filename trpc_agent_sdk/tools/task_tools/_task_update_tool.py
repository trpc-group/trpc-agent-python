# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""``task_update`` — incrementally patch one task by id."""

from __future__ import annotations

from typing import Any
from typing import List
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ._base import _TaskToolBase
from ._models import TaskStatus
from ._prompt import DEFAULT_TASK_UPDATE_DESCRIPTION
from ._store import add_blocked_by
from ._store import add_blocks
from ._store import clear_dependency
from ._store import remove_blocked_by
from ._store import remove_blocks
from ._validators import detect_cycle
from ._validators import validate_dependencies_exist
from ._validators import validate_status

_TOOL_NAME = "task_update"


def _as_str_list(value: Any) -> Optional[List[str]]:
    """Coerce an arg into a list of string ids, or ``None`` if absent/invalid."""
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    return [str(v) for v in value]


class TaskUpdateTool(_TaskToolBase):
    """Update a single task by id: status, fields, owner or dependencies.

    Args:
        enforce_single_in_progress: When ``True`` (default), reject setting a
            task ``in_progress`` while another task already is.
    """

    def __init__(self, *, enforce_single_in_progress: bool = True, **kwargs: Any) -> None:
        super().__init__(name=_TOOL_NAME, description=DEFAULT_TASK_UPDATE_DESCRIPTION, **kwargs)
        self._enforce_single_in_progress = bool(enforce_single_in_progress)

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        id_array = Schema(type=Type.ARRAY, items=Schema(type=Type.STRING))
        return FunctionDeclaration(
            name=self.name,
            description=DEFAULT_TASK_UPDATE_DESCRIPTION,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "taskId":
                    Schema(type=Type.STRING, description="Id of the task to update."),
                    "status":
                    Schema(
                        type=Type.STRING,
                        enum=["pending", "in_progress", "completed", "deleted"],
                        description="New status. Keep at most one task 'in_progress'.",
                    ),
                    "subject":
                    Schema(type=Type.STRING, description="New subject."),
                    "description":
                    Schema(type=Type.STRING, description="New description."),
                    "activeForm":
                    Schema(type=Type.STRING, description="New present-continuous form."),
                    "owner":
                    Schema(type=Type.STRING, description="Claiming agent / worker id."),
                    "addBlockedBy":
                    Schema(type=Type.ARRAY,
                           items=Schema(type=Type.STRING),
                           description="Upstream task ids to add as dependencies."),
                    "removeBlockedBy":
                    id_array,
                    "addBlocks":
                    Schema(type=Type.ARRAY,
                           items=Schema(type=Type.STRING),
                           description="Downstream task ids this task should block."),
                    "removeBlocks":
                    id_array,
                    "metadata":
                    Schema(type=Type.OBJECT, description="Replacement extension data."),
                },
                required=["taskId"],
            ),
        )

    @override
    async def _run_task_store(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        task_id = args.get("taskId")
        if not isinstance(task_id, str) or not task_id:
            return {"error": "INVALID_ARGS: `taskId` is required and must be a string"}

        store = self._load_store(tool_context)
        task = store.tasks.get(task_id)
        if task is None:
            return {"error": f"NOT_FOUND: task {task_id!r} does not exist"}

        new_status_raw = args.get("status")
        if new_status_raw is not None:
            if (err := validate_status(new_status_raw)) is not None:
                return {"error": f"INVALID_ARGS: {err}"}
        if task.status == TaskStatus.DELETED and new_status_raw not in (None, TaskStatus.DELETED.value):
            return {"error": f"INVALID_ARGS: task {task_id!r} is deleted and cannot be modified"}

        # Validate dependency reference existence before mutating anything.
        add_blocked = _as_str_list(args.get("addBlockedBy")) or []
        remove_blocked = _as_str_list(args.get("removeBlockedBy")) or []
        add_blk = _as_str_list(args.get("addBlocks")) or []
        remove_blk = _as_str_list(args.get("removeBlocks")) or []
        for ref in (add_blocked, add_blk):
            if (err := validate_dependencies_exist(store, ref)) is not None:
                return {"error": f"INVALID_DEPENDENCY: {err}"}

        # Single-in-progress guard: setting this task in_progress must not
        # leave two tasks in_progress at once.
        if (self._enforce_single_in_progress and new_status_raw == TaskStatus.IN_PROGRESS.value):
            others = [tid for tid, t in store.tasks.items() if t.status == TaskStatus.IN_PROGRESS and tid != task_id]
            if others:
                return {"error": f"INVALID_STATUS: task {others[0]!r} is already in_progress"}

        # Apply scalar field patches.
        if "subject" in args and args["subject"] is not None:
            if not isinstance(args["subject"], str) or not args["subject"].strip():
                return {"error": "INVALID_ARGS: `subject` must be a non-empty string"}
            task.subject = args["subject"]
        if "description" in args and args["description"] is not None:
            task.description = str(args["description"])
        if "activeForm" in args and args["activeForm"] is not None:
            task.active_form = str(args["activeForm"])
        if "owner" in args and args["owner"] is not None:
            task.owner = str(args["owner"])
        if "metadata" in args and args["metadata"] is not None:
            if not isinstance(args["metadata"], dict):
                return {"error": "INVALID_ARGS: `metadata` must be an object"}
            task.metadata = args["metadata"]

        # Apply dependency edits (two-way edges maintained by the store).
        if add_blocked:
            add_blocked_by(store, task_id, add_blocked)
        if remove_blocked:
            remove_blocked_by(store, task_id, remove_blocked)
        if add_blk:
            add_blocks(store, task_id, add_blk)
        if remove_blk:
            remove_blocks(store, task_id, remove_blk)

        if (err := detect_cycle(store)) is not None:
            # Reject the whole update; nothing has been persisted yet.
            return {"error": f"INVALID_DEPENDENCY: {err}"}

        # Apply status last so unblock computation reflects edited deps.
        unblocked: List[str] = []
        if new_status_raw is not None:
            task.status = TaskStatus(new_status_raw)
            if task.status == TaskStatus.COMPLETED:
                unblocked = clear_dependency(store, task_id)

        self._save_store(tool_context, store)

        return {
            "task": task.model_dump(mode="json", by_alias=True),
            "unblocked": unblocked,
            "message": _build_message(task_id, task.status, unblocked),
        }


def _build_message(task_id: str, status: TaskStatus, unblocked: List[str]) -> str:
    msg = f"Task {task_id} updated (status={status.value})."
    if unblocked:
        msg += f" Unblocked: {', '.join(unblocked)}."
    return msg
