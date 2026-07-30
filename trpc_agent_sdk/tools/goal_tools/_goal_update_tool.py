# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""``update_goal`` — move the active goal into a terminal state."""

from __future__ import annotations

import time
from typing import Any
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ._base import _GoalToolBase
from ._models import GoalStatus
from ._prompt import DEFAULT_GOAL_UPDATE_DESCRIPTION
from ._store import apply_transition

_TOOL_NAME = "update_goal"

# Terminal states the model is allowed to set; ``active`` is intentionally rejected.
_ALLOWED = {GoalStatus.COMPLETE.value, GoalStatus.BLOCKED.value}


class GoalUpdateTool(_GoalToolBase):
    """Transition the active goal to ``complete`` or ``blocked``."""

    def __init__(self, *, name: str = _TOOL_NAME, **kwargs: Any) -> None:
        super().__init__(name=name, description=DEFAULT_GOAL_UPDATE_DESCRIPTION, **kwargs)

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name=self.name,
            description=DEFAULT_GOAL_UPDATE_DESCRIPTION,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "status":
                    Schema(
                        type=Type.STRING,
                        enum=["complete", "blocked"],
                        description="'complete' if the objective is genuinely met, else 'blocked'.",
                    ),
                },
                required=["status"],
            ),
        )

    @override
    async def _run_goal(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        status_raw = args.get("status")
        if not isinstance(status_raw, str) or status_raw not in _ALLOWED:
            return {"error": "INVALID_ARGS: `status` must be 'complete' or 'blocked'"}

        existing = self._load_goal(tool_context)
        record, error = apply_transition(
            existing,
            status=GoalStatus(status_raw),
            now_unix=int(time.time()),
        )
        if error is not None:
            return {"error": f"INVALID_STATE: {error}"}

        self._save_goal(tool_context, record)
        return {
            "message": f"Goal marked {record.status.value}.",
            "goal": record.model_dump(mode="json", by_alias=True),
        }
