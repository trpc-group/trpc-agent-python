# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""``task_get`` — read the full details of a single task by id."""

from __future__ import annotations

from typing import Any
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ._base import _TaskToolBase
from ._prompt import DEFAULT_TASK_GET_DESCRIPTION

_TOOL_NAME = "task_get"


class TaskGetTool(_TaskToolBase):
    """Return the complete record (including description) for one task."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(name=_TOOL_NAME, description=DEFAULT_TASK_GET_DESCRIPTION, **kwargs)

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name=self.name,
            description=DEFAULT_TASK_GET_DESCRIPTION,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "taskId": Schema(type=Type.STRING, description="Id of the task to fetch."),
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

        return {"task": task.model_dump(mode="json", by_alias=True)}
