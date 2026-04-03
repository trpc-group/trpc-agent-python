# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Langchain knowledge search tool for TRPC Agent framework."""

from typing import Any
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.knowledge import KnowledgeFilterExpr
from trpc_agent_sdk.knowledge import SearchParams
from trpc_agent_sdk.knowledge import SearchRequest
from trpc_agent_sdk.knowledge import SearchResult
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ..langchain_knowledge import LangchainKnowledge
from ..langchain_knowledge import SearchType


class LangchainKnowledgeSearchTool(BaseTool):
    """Knowledge search tool with optional static knowledge filter."""

    def __init__(
        self,
        rag: LangchainKnowledge,
        top_k: int = 3,
        search_type: SearchType = SearchType.SIMILARITY,
        filters_name: Optional[list[str]] = None,
        filters: Optional[list[BaseFilter]] = None,
        *,
        min_score: float = 0.0,
        knowledge_filter: KnowledgeFilterExpr | None = None,
    ):
        name = "knowledge_search"
        description = "Search for relevant information in the knowledge base"
        super().__init__(
            name=name,
            description=description,
            filters_name=filters_name,
            filters=filters,
        )
        self.rag = rag
        self.top_k = top_k
        self.min_score = min_score
        self.search_type = search_type
        self.knowledge_filter = knowledge_filter

    @override
    def _get_declaration(self) -> Optional[FunctionDeclaration]:
        return FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "query": Schema(
                        type=Type.STRING,
                        description="The search query to find relevant documents",
                    ),
                },
                required=["query"],
            ),
        )

    def _build_search_params(self, knowledge_filter: KnowledgeFilterExpr | None) -> SearchParams:
        extra_params = self.rag.build_search_extra_params(knowledge_filter)
        return SearchParams(
            rank_top_k=self.top_k,
            search_type=self.search_type,
            extra_params=extra_params,
        )

    def _serialize_documents(self, search_result: SearchResult) -> list[dict[str, Any]]:
        serializable_documents: list[dict[str, Any]] = []
        for doc in search_result.documents:
            score_raw = doc.score
            score = float(score_raw) if isinstance(score_raw, (int, float)) else 0.0
            if score < self.min_score:
                continue
            serializable_doc = {
                "document": {
                    "page_content": doc.document.page_content,
                    "metadata": doc.document.metadata,
                },
                "score": score,
            }
            serializable_documents.append(serializable_doc)
        return serializable_documents

    async def _search_with_knowledge_filter(
        self,
        *,
        tool_context: InvocationContext,
        query: str,
        knowledge_filter: KnowledgeFilterExpr | None,
    ) -> list[dict[str, Any]]:
        agent_context = tool_context.agent_context
        if agent_context is None:
            agent_context = new_agent_context(timeout=3000)

        search_params = self._build_search_params(knowledge_filter)
        search_request = SearchRequest(params=search_params)
        search_request.query = Part.from_text(text=query)
        search_result: SearchResult = await self.rag.search(agent_context, search_request)
        if len(search_result.documents) == 0:
            return []
        return self._serialize_documents(search_result)

    async def _search(self, *, tool_context: InvocationContext, query: str) -> list[dict[str, Any]]:
        return await self._search_with_knowledge_filter(
            tool_context=tool_context,
            query=query,
            knowledge_filter=self.knowledge_filter,
        )

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        return await self._search(
            tool_context=tool_context,
            query=args["query"],
        )


class AgenticLangchainKnowledgeSearchTool(LangchainKnowledgeSearchTool):
    """Knowledge search tool with static and LLM-generated dynamic filter support."""

    def __init__(
        self,
        rag: LangchainKnowledge,
        top_k: int = 3,
        search_type: SearchType = SearchType.SIMILARITY,
        filters_name: Optional[list[str]] = None,
        filters: Optional[list[BaseFilter]] = None,
        *,
        min_score: float = 0.0,
        knowledge_filter: KnowledgeFilterExpr | None = None,
    ):
        super().__init__(
            rag=rag,
            top_k=top_k,
            search_type=search_type,
            filters_name=filters_name,
            filters=filters,
            min_score=min_score,
            knowledge_filter=knowledge_filter,
        )
        self.description = "Search knowledge with an optional dynamic_filter expression"

    @override
    def _get_declaration(self) -> Optional[FunctionDeclaration]:
        dynamic_filter_description = (
            "dynamic_filter: KnowledgeFilterExpr object.\n"
            "Fields:\n"
            "- field: metadata field path, e.g. metadata.category\n"
            "- operator: eq, ne, gt, gte, lt, lte, in, not in, like, not like, between, and, or\n"
            "- value: comparison value, or an array of sub-conditions for and/or\n"
            "Examples:\n"
            "1) {\"field\":\"metadata.category\",\"operator\":\"eq\",\"value\":\"machine-learning\"}\n"
            "2) {\"operator\":\"and\",\"value\":[{\"field\":\"metadata.status\",\"operator\":\"eq\",\"value\":\"active\"}]}"
        )
        return FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "query": Schema(
                        type=Type.STRING,
                        description="The search query to find relevant documents",
                    ),
                    "dynamic_filter": Schema(
                        type=Type.OBJECT,
                        description=dynamic_filter_description,
                    ),
                },
                required=["query"],
            ),
        )

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        dynamic_filter = args.get("dynamic_filter")
        if dynamic_filter is None:
            final_filter = self.knowledge_filter
        else:
            parsed_filter = KnowledgeFilterExpr.model_validate(dynamic_filter)
            if self.knowledge_filter is None:
                final_filter = parsed_filter
            else:
                final_filter = KnowledgeFilterExpr(
                    operator="and",
                    value=[self.knowledge_filter, parsed_filter],
                )
        return await self._search_with_knowledge_filter(
            tool_context=tool_context,
            query=args["query"],
            knowledge_filter=final_filter,
        )
