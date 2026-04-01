# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Preload memory tool for TRPC Agent framework."""

from __future__ import annotations

from typing import Any
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LlmRequest

from ._base_tool import BaseTool
from .utils import extract_text


class PreloadMemoryTool(BaseTool):
    """A tool that preloads the memory for the current user.

    NOTE: Currently this tool only uses text part from the memory.
    """

    def __init__(self):
        # Name and description are not used because this tool only
        # changes llm_request.
        super().__init__(name='preload_memory', description='preload_memory')

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        return {}

    @override
    async def process_request(
        self,
        *,
        tool_context: InvocationContext,
        llm_request: LlmRequest,
    ) -> None:
        user_content = tool_context.user_content
        if (not user_content or not user_content.parts or not user_content.parts[0].text):
            return

        user_query: str = user_content.parts[0].text
        response = await tool_context.search_memory(user_query)
        if not response.memories:
            return

        memory_text_lines = []
        for memory in response.memories:
            if time_str := (f'Time: {memory.timestamp}' if memory.timestamp else ''):
                memory_text_lines.append(time_str)
            if memory_text := extract_text(memory):
                memory_text_lines.append(f'{memory.author}: {memory_text}' if memory.author else memory_text)
        if not memory_text_lines:
            return

        full_memory_text = '\n'.join(memory_text_lines)
        si = f"""The following content is from your previous conversations with the user.
They may be useful for answering the user's current query.
<PAST_CONVERSATIONS>
{full_memory_text}
</PAST_CONVERSATIONS>
"""
        llm_request.append_instructions([si])


preload_memory_tool = PreloadMemoryTool()
