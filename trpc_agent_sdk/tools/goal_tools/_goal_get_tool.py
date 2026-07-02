# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""``get_goal`` — read the current persistent session goal."""

from __future__ import annotations

from typing import Any
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ._base import _GoalToolBase
from ._prompt import DEFAULT_GOAL_GET_DESCRIPTION

_TOOL_NAME = "get_goal"


class GoalGetTool(_GoalToolBase):
    """Return the current session goal, or report that none exists."""

    def __init__(self, *, name: str = _TOOL_NAME, **kwargs: Any) -> None:
        super().__init__(name=name, description=DEFAULT_GOAL_GET_DESCRIPTION, **kwargs)

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name=self.name,
            description=DEFAULT_GOAL_GET_DESCRIPTION,
            parameters=Schema(type=Type.OBJECT, properties={}),
        )

    @override
    async def _run_goal(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        goal = self._load_goal(tool_context)
        if goal is None:
            return {"message": "No session goal is set."}
        return {
            "message": f"Current goal is {goal.status.value}.",
            "goal": goal.model_dump(mode="json", by_alias=True),
        }
