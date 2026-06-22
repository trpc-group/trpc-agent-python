# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""``task_create`` — create a task and return its server-assigned id."""

from __future__ import annotations

from typing import Any
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ._base import _TaskToolBase
from ._prompt import DEFAULT_TASK_CREATE_DESCRIPTION
from ._store import create_task

_TOOL_NAME = "task_create"


class TaskCreateTool(_TaskToolBase):
    """Create a new task on the branch-scoped task board."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(name=_TOOL_NAME, description=DEFAULT_TASK_CREATE_DESCRIPTION, **kwargs)

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name=self.name,
            description=DEFAULT_TASK_CREATE_DESCRIPTION,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "subject":
                    Schema(
                        type=Type.STRING,
                        description="Short imperative title, e.g. 'Run tests'.",
                    ),
                    "description":
                    Schema(
                        type=Type.STRING,
                        description="Optional free-form details for the task.",
                    ),
                    "activeForm":
                    Schema(
                        type=Type.STRING,
                        description="Optional present-continuous form, e.g. 'Running tests'.",
                    ),
                    "metadata":
                    Schema(
                        type=Type.OBJECT,
                        description="Optional arbitrary key/value extension data.",
                    ),
                },
                required=["subject"],
            ),
        )

    @override
    async def _run_task_store(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        subject = args.get("subject")
        if not isinstance(subject, str) or not subject.strip():
            return {"error": "INVALID_ARGS: `subject` is required and must be a non-empty string"}

        description = args.get("description") or ""
        if not isinstance(description, str):
            return {"error": "INVALID_ARGS: `description` must be a string"}

        active_form = args.get("activeForm")
        if active_form is not None and not isinstance(active_form, str):
            return {"error": "INVALID_ARGS: `activeForm` must be a string"}

        metadata = args.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            return {"error": "INVALID_ARGS: `metadata` must be an object"}

        store = self._load_store(tool_context)
        record = create_task(
            store,
            subject=subject,
            description=description,
            active_form=active_form,
            metadata=metadata,
        )
        self._save_store(tool_context, store)

        return {
            "task": {
                "id": record.id,
                "subject": record.subject
            },
            "message": (f"Task {record.id} created. Use task_update to set status or dependencies."),
        }
