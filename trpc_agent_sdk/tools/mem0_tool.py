# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Mem0 memory function tools"""

from typing import Any
from typing import Optional
from typing import Union
from typing_extensions import override

from mem0 import AsyncMemory
from mem0 import AsyncMemoryClient

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ._base_tool import BaseTool

__all__ = ["SearchMemoryTool", "SaveMemoryTool"]


class SearchMemoryTool(BaseTool):
    """Search through past conversations and memories"""

    def __init__(self, client: Union[AsyncMemoryClient, AsyncMemory], **kwargs: Optional[dict[str, Any]]):
        filters_name = kwargs.pop("filters_name", None)
        filters = kwargs.pop("filters", None)
        super().__init__(name="search_memory",
                         description="Search through past conversations and memories",
                         filters_name=filters_name,
                         filters=filters)
        self.kwargs = kwargs or {}
        self.client = client

    @override
    def _get_declaration(self) -> FunctionDeclaration | None:
        return FunctionDeclaration(
            name="search_memory",
            description="Search through past conversations and memories",
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "query": Schema(
                        type=Type.STRING,
                        description="The query to search memory for.",
                    ),
                },
                required=["query"],
            ),
        )

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> dict:
        """Search through past conversations and memories"""
        user_id = tool_context.user_id
        memories = await self.client.search(query=args["query"], user_id=user_id, **self.kwargs)
        if memories.get('results', []):
            memory_list = memories['results']
            memory_context = "\n".join([f"- {mem['memory']}" for mem in memory_list])
            return {"status": "success", "memories": memory_context, "user_id": user_id}
        return {"status": "no_memories", "message": "No relevant memories found"}


class SaveMemoryTool(BaseTool):
    """Save important information to memory"""

    def __init__(self, client: Union[AsyncMemoryClient, AsyncMemory], **kwargs: Optional[dict[str, Any]]):
        filters_name = kwargs.pop("filters_name", None)
        filters = kwargs.pop("filters", None)
        super().__init__(name="save_memory",
                         description="Save important information to memory",
                         filters_name=filters_name,
                         filters=filters)
        self.kwargs = kwargs or {}
        if "infer" not in self.kwargs:
            self.kwargs["infer"] = True
        self.client = client

    @override
    def _get_declaration(self) -> FunctionDeclaration | None:
        return FunctionDeclaration(
            name="save_memory",
            description="Save important information to memory",
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "content": Schema(
                        type=Type.STRING,
                        description="The content to save to memory.",
                    ),
                },
                required=["content"],
            ),
        )

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> dict:
        """Save important information to memory"""
        user_id = tool_context.user_id
        try:
            result = await self.client.add([{
                "role": "user",
                "content": args["content"]
            }],
                                           user_id=user_id,
                                           **self.kwargs)
            return {"status": "success", "message": "Information saved to memory", "result": result, "user_id": user_id}
        except Exception as e:
            return {"status": "error", "message": f"Failed to save memory: {str(e)}", "user_id": user_id}
