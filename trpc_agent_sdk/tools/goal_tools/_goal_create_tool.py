# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""``create_goal`` — create a persistent, cross-turn session goal."""

from __future__ import annotations

import time
from typing import Any
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ._base import _GoalToolBase
from ._prompt import DEFAULT_GOAL_CREATE_DESCRIPTION
from ._store import apply_create

_TOOL_NAME = "create_goal"


class GoalCreateTool(_GoalToolBase):
    """Create a new ``active`` session goal (rejects a duplicate active goal)."""

    def __init__(self, *, name: str = _TOOL_NAME, **kwargs: Any) -> None:
        super().__init__(name=name, description=DEFAULT_GOAL_CREATE_DESCRIPTION, **kwargs)

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name=self.name,
            description=DEFAULT_GOAL_CREATE_DESCRIPTION,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "objective":
                    Schema(
                        type=Type.STRING,
                        description="The completion criterion: what 'done' concretely means.",
                    ),
                },
                required=["objective"],
            ),
        )

    @override
    async def _run_goal(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        objective = args.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            return {"error": "INVALID_ARGS: `objective` is required and must be a non-empty string"}

        existing = self._load_goal(tool_context)
        record, error = apply_create(existing, objective=objective.strip(), now_unix=int(time.time()))
        if error is not None:
            return {"error": f"INVALID_STATE: {error}"}

        self._save_goal(tool_context, record)
        return {
            "message": "Goal created and is now active. Keep working until it is genuinely met, "
            "then call update_goal('complete').",
            "goal": record.model_dump(mode="json", by_alias=True),
        }
