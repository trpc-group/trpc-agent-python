# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""PlanToolSet — bundles Plan Mode tools."""

from __future__ import annotations

from typing import List
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.abc import ToolSetABC
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools._base_tool import BaseTool

from ._helpers import DEFAULT_FORCE_ENTER_PLAN_STATE_KEY
from ._helpers import DEFAULT_FORCE_ENTER_PLAN_STATE_VALUE
from ._helpers import DEFAULT_STATE_KEY_PREFIX
from ._helpers import should_hide_enter_plan_mode_tool
from ._long_running_tools import make_ask_user_question_tool
from ._long_running_tools import make_enter_plan_mode_tool
from ._long_running_tools import make_exit_plan_mode_tool
from ._update_plan_content_tool import UpdatePlanContentTool


class PlanToolSet(ToolSetABC):
    """Toolset for session-scoped Plan Mode (enter / draft / approve / exit)."""

    def __init__(
        self,
        *,
        state_key_prefix: str = DEFAULT_STATE_KEY_PREFIX,
        force_enter_plan_state_key: Optional[str] = DEFAULT_FORCE_ENTER_PLAN_STATE_KEY,
        force_enter_plan_state_value: str = DEFAULT_FORCE_ENTER_PLAN_STATE_VALUE,
        name: str = "plan_toolset",
    ) -> None:
        super().__init__(name=name)
        self._prefix = state_key_prefix or DEFAULT_STATE_KEY_PREFIX
        self._force_enter_plan_state_key = force_enter_plan_state_key
        self._force_enter_plan_state_value = force_enter_plan_state_value

    @override
    async def get_tools(self, invocation_context: Optional[InvocationContext] = None) -> List[BaseTool]:
        kwargs = {"state_key_prefix": self._prefix}
        tools: List[BaseTool] = [
            make_enter_plan_mode_tool(**kwargs),
            UpdatePlanContentTool(**kwargs),
            make_exit_plan_mode_tool(**kwargs),
            make_ask_user_question_tool(**kwargs),
        ]
        if should_hide_enter_plan_mode_tool(
                invocation_context,
                state_key_prefix=self._prefix,
                force_enter_plan_state_key=self._force_enter_plan_state_key,
                force_enter_plan_state_value=self._force_enter_plan_state_value,
        ):
            tools = [tool for tool in tools if tool.name != "enter_plan_mode"]
        return tools
