# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared base for the Goal tools: branch resolution and goal load / save."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter

from .._base_tool import BaseTool
from ._helpers import DEFAULT_STATE_KEY_PREFIX
from ._helpers import decode_goal
from ._helpers import encode_goal
from ._helpers import state_key
from ._lock import goal_store_lock
from ._models import GoalRecord


class _GoalToolBase(BaseTool):
    """Common plumbing shared by the three Goal tools.

    Handles branch-scoped state-key resolution and goal load / save. The
    behavioural guidance is injected by the enforcement callbacks (see
    :mod:`._setup`), not here, so the tools stay lightweight and can be
    mounted independently.
    """

    def __init__(
        self,
        *,
        name: str,
        description: str,
        state_key_prefix: str = DEFAULT_STATE_KEY_PREFIX,
        filters_name: Optional[list[str]] = None,
        filters: Optional[list[BaseFilter]] = None,
    ) -> None:
        super().__init__(
            name=name,
            description=description,
            filters_name=filters_name,
            filters=filters,
        )
        self._prefix = state_key_prefix or DEFAULT_STATE_KEY_PREFIX

    def _resolve_branch(self, tool_context: InvocationContext) -> str:
        return tool_context.branch or tool_context.agent_name or ""

    def _state_key(self, tool_context: InvocationContext) -> str:
        return state_key(self._prefix, self._resolve_branch(tool_context))

    def _load_goal(self, tool_context: InvocationContext) -> Optional[GoalRecord]:
        return decode_goal(tool_context.state.get(self._state_key(tool_context)))

    def _save_goal(self, tool_context: InvocationContext, goal: GoalRecord) -> None:
        tool_context.state[self._state_key(tool_context)] = encode_goal(goal)

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        branch = self._resolve_branch(tool_context)
        async with goal_store_lock(tool_context, prefix=self._prefix, branch=branch):
            return await self._run_goal(tool_context=tool_context, args=args)

    @abstractmethod
    async def _run_goal(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        """Tool logic; called under :func:`goal_store_lock` for the branch goal."""
