# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# This file is part of tRPC-Agent-Python and is licensed under Apache-2.0.
#
# Portions of this file are derived from HKUDS/nanobot (MIT License):
# https://github.com/HKUDS/nanobot.git
#
# Copyright (c) 2025 nanobot contributors
#
# See the project LICENSE / third-party attribution notices for details.
#
"""Background task spawn tool.

This tool provides trpc-claw-like asynchronous task spawning behavior without
depending on trpc-claw's worker agent implementation.
"""

from __future__ import annotations

from typing import Any
from typing import Awaitable
from typing import Callable
from typing import List
from typing import Optional

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

SPAWN_TASK_SUBMIT_CALLBACK_KEY = "spawn_task_submit_callback"
SPAWN_TASK_CHANNEL_KEY = "spawn_task_channel"
SPAWN_TASK_CHAT_ID_KEY = "spawn_task_chat_id"
SPAWN_TASK_SESSION_KEY = "spawn_task_session_key"
SPAWN_TASK_USER_ID_KEY = "spawn_task_user_id"

_DESCRIPTION = ("Start a background task and return immediately. "
                "Use this for long-running work that should not block the current conversation.")


class SpawnTaskTool(BaseTool):
    """Spawn a background task through runtime-injected callback."""

    def __init__(
        self,
        filters_name: Optional[List[str]] = None,
        filters: Optional[List[BaseFilter]] = None,
    ) -> None:
        super().__init__(
            name="spawn_task",
            description=_DESCRIPTION,
            filters_name=filters_name,
            filters=filters,
        )

    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name="spawn_task",
            description=_DESCRIPTION,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "task": Schema(
                        type=Type.STRING,
                        description="Background task instruction for the worker agent",
                    ),
                    "label": Schema(
                        type=Type.STRING,
                        description="Optional short display label for the task",
                    ),
                    "channel": Schema(
                        type=Type.STRING,
                        description="Optional override target channel",
                    ),
                    "chat_id": Schema(
                        type=Type.STRING,
                        description="Optional override target chat id",
                    ),
                },
                required=["task"],
            ),
        )

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        agent_ctx = tool_context.agent_context
        submit_callback: Optional[Callable[..., Awaitable[str]
                                           | str]] = (agent_ctx.get_metadata(SPAWN_TASK_SUBMIT_CALLBACK_KEY)
                                                      if agent_ctx else None)
        if not submit_callback:
            return (f"Error: spawn callback not configured — set "
                    f"{SPAWN_TASK_SUBMIT_CALLBACK_KEY!r} in agent_context metadata.")

        task = args.get("task", "")
        if not task:
            return "Error: task is required"

        channel = args.get("channel") or (agent_ctx.get_metadata(SPAWN_TASK_CHANNEL_KEY, "") if agent_ctx else "")
        chat_id = args.get("chat_id") or (agent_ctx.get_metadata(SPAWN_TASK_CHAT_ID_KEY, "") if agent_ctx else "")
        session_key = agent_ctx.get_metadata(SPAWN_TASK_SESSION_KEY, "") if agent_ctx else ""
        user_id = agent_ctx.get_metadata(SPAWN_TASK_USER_ID_KEY, "") if agent_ctx else ""
        label = args.get("label")

        if not channel or not chat_id or not session_key:
            return ("Error: missing runtime context for spawn_task. "
                    "Required metadata: channel/chat_id/session_key.")

        result = submit_callback(
            task=task,
            label=label,
            origin_channel=channel,
            origin_chat_id=chat_id,
            session_key=session_key,
            user_id=user_id or "user",
        )
        if hasattr(result, "__await__"):
            return await result  # type: ignore[return-value]
        return result
