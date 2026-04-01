# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Knowledge search node action executor."""

from typing import Any
from typing import Callable
from typing import Optional
from typing import Union

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.server.knowledge.tools import LangchainKnowledgeSearchTool

from .._constants import STATE_KEY_LAST_RESPONSE
from .._constants import STATE_KEY_NODE_RESPONSES
from .._event_writer import AsyncEventWriter
from .._event_writer import EventWriter
from .._state import State
from ._base import BaseNodeAction


class KnowledgeNodeAction(BaseNodeAction):
    """Execute a knowledge search tool and map results into graph state."""

    def __init__(
        self,
        name: str,
        query: Union[str, Callable[[State], str]],
        tool: LangchainKnowledgeSearchTool,
        writer: EventWriter,
        async_writer: AsyncEventWriter,
        ctx: Optional[InvocationContext] = None,
    ):
        super().__init__(name, writer, async_writer, ctx)
        self.query = query
        self.tool = tool

    def _resolve_query(self, state: State) -> str:
        if callable(self.query):
            value = self.query(state)
        else:
            value = self.query
        if not isinstance(value, str):
            return str(value)
        return value

    async def _invoke_tool(self, query: str) -> Any:
        if self.ctx is None:
            raise RuntimeError(
                f"Knowledge node '{self.name}' requires InvocationContext but none was set. "
                "Pass context via config['configurable']['invocation_context'] when executing the graph.")
        return await self.tool.run_async(tool_context=self.ctx, args={"query": query})

    @staticmethod
    def _normalize_documents(result: Any) -> tuple[list[dict[str, Any]], str]:
        message = ""
        raw_documents: Any = result
        if isinstance(result, dict):
            raw_documents = result.get("documents", [])
            raw_message = result.get("message")
            if isinstance(raw_message, str):
                message = raw_message

        if not isinstance(raw_documents, list):
            raw_documents = []

        documents: list[dict[str, Any]] = []
        for item in raw_documents:
            if isinstance(item, dict) and "text" in item:
                doc = {
                    "text": item.get("text", ""),
                    "score": item.get("score", 0.0),
                }
                metadata = item.get("metadata")
                if metadata is not None:
                    doc["metadata"] = metadata
                documents.append(doc)
                continue

            if isinstance(item, dict):
                document_obj = item.get("document")
                text = ""
                metadata: Any = None
                if isinstance(document_obj, dict):
                    text = str(document_obj.get("page_content", ""))
                    metadata = document_obj.get("metadata")
                score = item.get("score", 0.0)
                doc = {
                    "text": text,
                    "score": score,
                }
                if metadata is not None:
                    doc["metadata"] = metadata
                documents.append(doc)

        return documents, message

    async def execute(self, state: State) -> dict[str, Any]:
        query = self._resolve_query(state)
        result = await self._invoke_tool(query)
        documents, message = self._normalize_documents(result)

        payload: dict[str, Any] = {"documents": documents}
        if message:
            payload["message"] = message

        logger.info(f"Query: [{query}], Result: [{str(payload['documents'])[:500]}]")

        return {
            STATE_KEY_LAST_RESPONSE: payload,
            STATE_KEY_NODE_RESPONSES: {
                self.name: payload
            },
        }
