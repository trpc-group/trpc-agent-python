# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared base for the Task tools: branch resolution, store I/O, prompt injection."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.models import LlmRequest

from .._base_tool import BaseTool
from ._helpers import DEFAULT_STATE_KEY_PREFIX
from ._helpers import decode_store
from ._helpers import encode_store
from ._helpers import state_key
from ._lock import task_store_lock
from ._models import TaskStore
from ._prompt import DEFAULT_TASK_PROMPT
from ._prompt import _PROMPT_MARKER


class _TaskToolBase(BaseTool):
    """Common plumbing shared by the four Task tools.

    Handles branch-scoped state-key resolution, store load / save, and
    one-time injection of :data:`DEFAULT_TASK_PROMPT` into the system
    instruction (guarded so mounting several task tools does not duplicate
    the guidance).
    """

    def __init__(
        self,
        *,
        name: str,
        description: str,
        state_key_prefix: str = DEFAULT_STATE_KEY_PREFIX,
        inject_prompt: bool = True,
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
        self._inject_prompt = inject_prompt

    def _resolve_branch(self, tool_context: InvocationContext) -> str:
        return tool_context.branch or tool_context.agent_name or ""

    def _load_store(self, tool_context: InvocationContext) -> TaskStore:
        branch = self._resolve_branch(tool_context)
        return decode_store(tool_context.state.get(state_key(self._prefix, branch)))

    def _save_store(self, tool_context: InvocationContext, store: TaskStore) -> None:
        branch = self._resolve_branch(tool_context)
        tool_context.state[state_key(self._prefix, branch)] = encode_store(store)

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        branch = self._resolve_branch(tool_context)
        async with task_store_lock(tool_context, prefix=self._prefix, branch=branch):
            return await self._run_task_store(tool_context=tool_context, args=args)

    @abstractmethod
    async def _run_task_store(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        """Tool logic; called under :func:`task_store_lock` for the branch board."""

    @override
    async def process_request(self, *, tool_context: InvocationContext, llm_request: LlmRequest) -> None:
        await super().process_request(tool_context=tool_context, llm_request=llm_request)
        if not self._inject_prompt:
            return
        existing = ""
        if llm_request.config and llm_request.config.system_instruction:
            existing = str(llm_request.config.system_instruction)
        if _PROMPT_MARKER not in existing:
            llm_request.append_instructions([DEFAULT_TASK_PROMPT])
