# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Shared base for Plan Mode tools."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.tools._base_tool import BaseTool

from ._helpers import DEFAULT_STATE_KEY_PREFIX
from ._helpers import decode_plan
from ._helpers import encode_plan
from ._helpers import state_key
from ._lock import plan_store_lock
from ._models import PlanRecord


class _PlanToolBase(BaseTool):
    """Branch-scoped plan load / save shared by all plan tools."""

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

    def _load_plan(self, tool_context: InvocationContext) -> Optional[PlanRecord]:
        return decode_plan(tool_context.state.get(self._state_key(tool_context)))

    def _save_plan(self, tool_context: InvocationContext, plan: PlanRecord) -> None:
        branch = self._resolve_branch(tool_context)
        if branch:
            plan.branch = branch
        tool_context.state[self._state_key(tool_context)] = encode_plan(plan)

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        branch = self._resolve_branch(tool_context)
        async with plan_store_lock(tool_context, prefix=self._prefix, branch=branch):
            return await self._run_plan(tool_context=tool_context, args=args)

    @abstractmethod
    async def _run_plan(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        """Tool logic under plan_store_lock."""
