# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Load memory tool for TRPC Agent."""

from __future__ import annotations

import json
from typing import Any
from typing_extensions import override

from pydantic import BaseModel
from pydantic import Field

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import MemoryEntry
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ._function_tool import FunctionTool


class LoadMemoryResponse(BaseModel):
    """Response for the load memory tool."""
    memories: list[MemoryEntry] = Field(default_factory=list)


async def load_memory(query: str, tool_context: InvocationContext) -> dict[str, Any]:
    """Loads the memory for the current user.

  Args:
    query: The query to load the memory for.

  Returns:
    A list of memory results.
  """
    search_memory_response = await tool_context.search_memory(query)
    rsp = LoadMemoryResponse(memories=search_memory_response.memories)
    return json.dumps(rsp.model_dump())


class LoadMemoryTool(FunctionTool):
    """A tool that loads the memory for the current user.

    NOTE: Currently this tool only uses text part from the memory.
    """

    def __init__(self):
        super().__init__(load_memory)

    @override
    def _get_declaration(self) -> FunctionDeclaration | None:
        return FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=Schema(
                type=Type.OBJECT,
                properties={'query': Schema(type=Type.STRING, )},
            ),
        )

    @override
    async def process_request(
        self,
        *,
        tool_context: InvocationContext,
        llm_request: LlmRequest,
    ) -> None:
        await super().process_request(tool_context=tool_context, llm_request=llm_request)
        # Tell the model about the memory.
        llm_request.append_instructions([
            """
You have memory. You can use it to answer questions. If any questions need
you to look up the memory, you should call load_memory function with a query.
"""
        ])


load_memory_tool = LoadMemoryTool()
