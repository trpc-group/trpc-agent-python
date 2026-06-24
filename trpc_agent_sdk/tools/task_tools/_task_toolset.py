# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""``TaskToolSet`` — bundles the four Task tools as a single toolset."""

from __future__ import annotations

from typing import List
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.abc import ToolSetABC
from trpc_agent_sdk.context import InvocationContext

from .._base_tool import BaseTool
from ._helpers import DEFAULT_STATE_KEY_PREFIX
from ._task_create_tool import TaskCreateTool
from ._task_get_tool import TaskGetTool
from ._task_list_tool import TaskListTool
from ._task_update_tool import TaskUpdateTool


class TaskToolSet(ToolSetABC):
    """Toolset exposing ``task_create`` / ``task_update`` / ``task_get`` / ``task_list``.

    The structured task board aligns with Claude Code's Task tools: tasks are
    created with server-assigned ids and updated incrementally by id, with
    ``blockedBy`` / ``blocks`` dependency edges. The board is persisted to
    branch-scoped session state and survives across Runner invocations.

    Args:
        state_key_prefix: State-key prefix; ``tasks`` by default. Avoid
            ``temp:`` — that prefix is invocation-only and is not stored.
        enforce_single_in_progress: Reject setting a task ``in_progress``
            while another already is (default ``True``).
        inject_prompt: Inject :data:`DEFAULT_TASK_PROMPT` into the system
            instruction once (default ``True``).
    """

    def __init__(
        self,
        *,
        state_key_prefix: str = DEFAULT_STATE_KEY_PREFIX,
        enforce_single_in_progress: bool = True,
        inject_prompt: bool = True,
        name: str = "task_toolset",
    ) -> None:
        super().__init__(name=name)
        self._prefix = state_key_prefix or DEFAULT_STATE_KEY_PREFIX
        self._enforce_single_in_progress = bool(enforce_single_in_progress)
        self._inject_prompt = bool(inject_prompt)

    @override
    async def get_tools(self, invocation_context: Optional[InvocationContext] = None) -> List[BaseTool]:
        return [
            TaskCreateTool(state_key_prefix=self._prefix, inject_prompt=self._inject_prompt),
            TaskUpdateTool(
                state_key_prefix=self._prefix,
                inject_prompt=self._inject_prompt,
                enforce_single_in_progress=self._enforce_single_in_progress,
            ),
            TaskGetTool(state_key_prefix=self._prefix, inject_prompt=self._inject_prompt),
            TaskListTool(state_key_prefix=self._prefix, inject_prompt=self._inject_prompt),
        ]
