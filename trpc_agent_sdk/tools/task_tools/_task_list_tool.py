# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""``task_list`` — list all tasks as a compact, token-optimised summary."""

from __future__ import annotations

from typing import Any
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ._base import _TaskToolBase
from ._prompt import DEFAULT_TASK_LIST_DESCRIPTION
from ._store import list_summaries
from ._store import stats

_TOOL_NAME = "task_list"


class TaskListTool(_TaskToolBase):
    """List task summaries (no descriptions) plus per-status counts."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(name=_TOOL_NAME, description=DEFAULT_TASK_LIST_DESCRIPTION, **kwargs)

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name=self.name,
            description=DEFAULT_TASK_LIST_DESCRIPTION,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "includeDeleted":
                    Schema(
                        type=Type.BOOLEAN,
                        description="Include soft-deleted tasks in the list (default false).",
                    ),
                },
            ),
        )

    @override
    async def _run_task_store(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        include_deleted = bool(args.get("includeDeleted", False))
        store = self._load_store(tool_context)
        summaries = list_summaries(store, include_deleted=include_deleted)
        return {
            "tasks": [s.model_dump(mode="json", by_alias=True) for s in summaries],
            "stats": stats(store),
        }
