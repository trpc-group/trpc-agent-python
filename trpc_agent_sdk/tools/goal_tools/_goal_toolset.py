# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""``GoalToolSet`` — bundles ``get_goal`` / ``create_goal`` / ``update_goal``."""

from __future__ import annotations

from typing import List
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.abc import ToolSetABC
from trpc_agent_sdk.context import InvocationContext

from .._base_tool import BaseTool
from ._goal_create_tool import GoalCreateTool
from ._goal_get_tool import GoalGetTool
from ._goal_update_tool import GoalUpdateTool
from ._helpers import DEFAULT_STATE_KEY_PREFIX


class GoalToolSet(ToolSetABC):
    """Toolset exposing ``get_goal`` / ``create_goal`` / ``update_goal``.

    The goal is a single, session-scoped contract persisted to branch-scoped
    session state and surviving across Runner invocations. Mount it together
    with the enforcement callbacks via
    :func:`trpc_agent_sdk.tools.goal_tools.setup_goal`, or on its own when you
    only want the model-facing tools.

    Args:
        state_key_prefix: State-key prefix; ``goal`` by default. Avoid
            ``temp:`` — that prefix is invocation-only and is not stored.
    """

    def __init__(
        self,
        *,
        state_key_prefix: str = DEFAULT_STATE_KEY_PREFIX,
        name: str = "goal_toolset",
    ) -> None:
        super().__init__(name=name)
        self._prefix = state_key_prefix or DEFAULT_STATE_KEY_PREFIX

    @override
    async def get_tools(self, invocation_context: Optional[InvocationContext] = None) -> List[BaseTool]:
        return [
            GoalGetTool(name="get_goal", state_key_prefix=self._prefix),
            GoalCreateTool(name="create_goal", state_key_prefix=self._prefix),
            GoalUpdateTool(name="update_goal", state_key_prefix=self._prefix),
        ]
