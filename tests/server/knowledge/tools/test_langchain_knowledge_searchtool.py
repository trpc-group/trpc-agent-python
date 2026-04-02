# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for trpc_agent_sdk.server.knowledge.tools.langchain_knowledge_searchtool module."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.documents import Document

from trpc_agent_sdk.context import AgentContext, InvocationContext, create_agent_context
from trpc_agent_sdk.knowledge import (
    KnowledgeFilterExpr,
    SearchDocument,
    SearchParams,
    SearchRequest,
    SearchResult,
)
from trpc_agent_sdk.server.knowledge.langchain_knowledge import (
    LangchainKnowledge,
    SearchType,
)
from trpc_agent_sdk.server.knowledge.tools.langchain_knowledge_searchtool import (
    AgenticLangchainKnowledgeSearchTool,
    LangchainKnowledgeSearchTool,
)
from trpc_agent_sdk.types import Part, Schema, Type


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_rag(search_result: SearchResult | None = None) -> LangchainKnowledge:
    rag = MagicMock(spec=LangchainKnowledge)
    rag.build_search_extra_params.return_value = {}
    rag.search = AsyncMock(
        return_value=search_result
        if search_result is not None
        else SearchResult(documents=[])
    )
    return rag


def _mock_invocation_context(agent_context: AgentContext | None = None) -> InvocationContext:
    ctx = MagicMock(spec=InvocationContext)
    ctx.agent_context = agent_context
    return ctx


def _search_result_with_docs(*contents_and_scores) -> SearchResult:
    docs = []
    for content, score in contents_and_scores:
        docs.append(
            SearchDocument(
                document=Document(page_content=content, metadata={"src": "test"}),
                score=score,
            )
        )
    return SearchResult(documents=docs)


# ---------------------------------------------------------------------------
# LangchainKnowledgeSearchTool.__init__
# ---------------------------------------------------------------------------


class TestLangchainKnowledgeSearchToolInit:
    def test_defaults(self):
        rag = _mock_rag()
        tool = LangchainKnowledgeSearchTool(rag=rag)
        assert tool.name == "knowledge_search"
        assert tool.description == "Search for relevant information in the knowledge base"
        assert tool.rag is rag
        assert tool.top_k == 3
        assert tool.min_score == 0.0
        assert tool.search_type == SearchType.SIMILARITY
        assert tool.knowledge_filter is None

    def test_custom_params(self):
        rag = _mock_rag()
        kf = KnowledgeFilterExpr(operator="eq", field="status", value="active")
        tool = LangchainKnowledgeSearchTool(
            rag=rag,
            top_k=10,
            search_type=SearchType.MAX_MARGINAL_RELEVANCE,
            min_score=0.5,
            knowledge_filter=kf,
        )
        assert tool.top_k == 10
        assert tool.search_type == SearchType.MAX_MARGINAL_RELEVANCE
        assert tool.min_score == 0.5
        assert tool.knowledge_filter is kf


# ---------------------------------------------------------------------------
# _get_declaration
# ---------------------------------------------------------------------------


class TestGetDeclaration:
    def test_declaration_schema(self):
        tool = LangchainKnowledgeSearchTool(rag=_mock_rag())
        decl = tool._get_declaration()
        assert decl is not None
        assert decl.name == "knowledge_search"
        assert decl.parameters.type == Type.OBJECT
        assert "query" in decl.parameters.properties
        assert decl.parameters.required == ["query"]

    def test_agentic_declaration_has_dynamic_filter(self):
        tool = AgenticLangchainKnowledgeSearchTool(rag=_mock_rag())
        decl = tool._get_declaration()
        assert decl is not None
        assert "query" in decl.parameters.properties
        assert "dynamic_filter" in decl.parameters.properties
        assert decl.parameters.required == ["query"]


# ---------------------------------------------------------------------------
# _build_search_params
# ---------------------------------------------------------------------------


class TestBuildSearchParams:
    def test_without_filter(self):
        rag = _mock_rag()
        rag.build_search_extra_params.return_value = {}
        tool = LangchainKnowledgeSearchTool(rag=rag, top_k=5, search_type=SearchType.SIMILARITY)
        params = tool._build_search_params(None)
        assert isinstance(params, SearchParams)
        assert params.rank_top_k == 5
        assert params.search_type == SearchType.SIMILARITY.value
        rag.build_search_extra_params.assert_called_once_with(None)

    def test_with_filter(self):
        rag = _mock_rag()
        rag.build_search_extra_params.return_value = {"filter": {"key": "val"}}
        kf = KnowledgeFilterExpr(operator="eq", field="f", value="v")
        tool = LangchainKnowledgeSearchTool(rag=rag, top_k=3)
        params = tool._build_search_params(kf)
        assert params.extra_params == {"filter": {"key": "val"}}
        rag.build_search_extra_params.assert_called_once_with(kf)


# ---------------------------------------------------------------------------
# _serialize_documents
# ---------------------------------------------------------------------------


class TestSerializeDocuments:
    def test_empty_result(self):
        tool = LangchainKnowledgeSearchTool(rag=_mock_rag())
        result = SearchResult(documents=[])
        serialized = tool._serialize_documents(result)
        assert serialized == []

    def test_all_above_min_score(self):
        tool = LangchainKnowledgeSearchTool(rag=_mock_rag(), min_score=0.0)
        result = _search_result_with_docs(("doc1", 0.9), ("doc2", 0.7))
        serialized = tool._serialize_documents(result)
        assert len(serialized) == 2
        assert serialized[0]["document"]["page_content"] == "doc1"
        assert serialized[0]["score"] == 0.9
        assert serialized[1]["document"]["page_content"] == "doc2"

    def test_filters_below_min_score(self):
        tool = LangchainKnowledgeSearchTool(rag=_mock_rag(), min_score=0.5)
        result = _search_result_with_docs(("high", 0.8), ("low", 0.3))
        serialized = tool._serialize_documents(result)
        assert len(serialized) == 1
        assert serialized[0]["document"]["page_content"] == "high"

    def test_all_below_min_score(self):
        tool = LangchainKnowledgeSearchTool(rag=_mock_rag(), min_score=0.99)
        result = _search_result_with_docs(("a", 0.5), ("b", 0.6))
        serialized = tool._serialize_documents(result)
        assert serialized == []

    def test_none_score_defaults_to_zero(self):
        """When score_raw is not int/float, _serialize_documents coerces to 0.0."""
        tool = LangchainKnowledgeSearchTool(rag=_mock_rag(), min_score=0.0)
        doc = SearchDocument(
            document=Document(page_content="x", metadata={}),
            score=0.0,
        )
        doc.__dict__["score"] = None
        result = SearchResult(documents=[doc])
        serialized = tool._serialize_documents(result)
        assert len(serialized) == 1
        assert serialized[0]["score"] == 0.0

    def test_metadata_preserved(self):
        tool = LangchainKnowledgeSearchTool(rag=_mock_rag())
        doc = SearchDocument(
            document=Document(page_content="c", metadata={"author": "Alice"}),
            score=0.5,
        )
        result = SearchResult(documents=[doc])
        serialized = tool._serialize_documents(result)
        assert serialized[0]["document"]["metadata"] == {"author": "Alice"}

    def test_integer_score(self):
        tool = LangchainKnowledgeSearchTool(rag=_mock_rag(), min_score=0.0)
        doc = SearchDocument(
            document=Document(page_content="int_score"),
            score=1,
        )
        result = SearchResult(documents=[doc])
        serialized = tool._serialize_documents(result)
        assert serialized[0]["score"] == 1.0
        assert isinstance(serialized[0]["score"], float)


# ---------------------------------------------------------------------------
# _search_with_knowledge_filter
# ---------------------------------------------------------------------------


class TestSearchWithKnowledgeFilter:
    @pytest.mark.asyncio
    async def test_uses_provided_agent_context(self):
        result = _search_result_with_docs(("found", 0.8))
        rag = _mock_rag(result)
        tool = LangchainKnowledgeSearchTool(rag=rag)
        agent_ctx = create_agent_context()
        inv_ctx = _mock_invocation_context(agent_ctx)
        docs = await tool._search_with_knowledge_filter(
            tool_context=inv_ctx, query="q", knowledge_filter=None
        )
        rag.search.assert_awaited_once()
        search_call_args = rag.search.call_args[0]
        assert search_call_args[0] is agent_ctx

    @pytest.mark.asyncio
    async def test_creates_agent_context_when_none(self):
        result = _search_result_with_docs(("found", 0.8))
        rag = _mock_rag(result)
        tool = LangchainKnowledgeSearchTool(rag=rag)
        inv_ctx = _mock_invocation_context(None)
        docs = await tool._search_with_knowledge_filter(
            tool_context=inv_ctx, query="q", knowledge_filter=None
        )
        rag.search.assert_awaited_once()
        search_call_args = rag.search.call_args[0]
        assert isinstance(search_call_args[0], AgentContext)

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_list(self):
        rag = _mock_rag(SearchResult(documents=[]))
        tool = LangchainKnowledgeSearchTool(rag=rag)
        inv_ctx = _mock_invocation_context(create_agent_context())
        docs = await tool._search_with_knowledge_filter(
            tool_context=inv_ctx, query="q", knowledge_filter=None
        )
        assert docs == []

    @pytest.mark.asyncio
    async def test_passes_knowledge_filter_to_build_params(self):
        rag = _mock_rag(_search_result_with_docs(("doc", 0.5)))
        kf = KnowledgeFilterExpr(operator="eq", field="x", value="y")
        tool = LangchainKnowledgeSearchTool(rag=rag)
        inv_ctx = _mock_invocation_context(create_agent_context())
        await tool._search_with_knowledge_filter(
            tool_context=inv_ctx, query="q", knowledge_filter=kf
        )
        rag.build_search_extra_params.assert_called_once_with(kf)

    @pytest.mark.asyncio
    async def test_search_request_has_correct_query(self):
        rag = _mock_rag(_search_result_with_docs(("doc", 0.5)))
        tool = LangchainKnowledgeSearchTool(rag=rag, top_k=7)
        inv_ctx = _mock_invocation_context(create_agent_context())
        await tool._search_with_knowledge_filter(
            tool_context=inv_ctx, query="my query", knowledge_filter=None
        )
        search_call_args = rag.search.call_args[0]
        req: SearchRequest = search_call_args[1]
        assert req.query.text == "my query"
        assert req.params.rank_top_k == 7


# ---------------------------------------------------------------------------
# _search / _run_async_impl (LangchainKnowledgeSearchTool)
# ---------------------------------------------------------------------------


class TestLangchainKnowledgeSearchToolRun:
    @pytest.mark.asyncio
    async def test_search_delegates_to_search_with_filter(self):
        result = _search_result_with_docs(("r", 0.7))
        rag = _mock_rag(result)
        kf = KnowledgeFilterExpr(operator="eq", field="f", value="v")
        tool = LangchainKnowledgeSearchTool(rag=rag, knowledge_filter=kf)
        inv_ctx = _mock_invocation_context(create_agent_context())
        docs = await tool._search(tool_context=inv_ctx, query="q")
        rag.build_search_extra_params.assert_called_once_with(kf)

    @pytest.mark.asyncio
    async def test_run_async_impl(self):
        result = _search_result_with_docs(("impl", 0.9))
        rag = _mock_rag(result)
        tool = LangchainKnowledgeSearchTool(rag=rag)
        inv_ctx = _mock_invocation_context(create_agent_context())
        docs = await tool._run_async_impl(
            tool_context=inv_ctx, args={"query": "test"}
        )
        assert len(docs) == 1
        assert docs[0]["document"]["page_content"] == "impl"


# ---------------------------------------------------------------------------
# AgenticLangchainKnowledgeSearchTool.__init__
# ---------------------------------------------------------------------------


class TestAgenticInit:
    def test_description_overridden(self):
        tool = AgenticLangchainKnowledgeSearchTool(rag=_mock_rag())
        assert "dynamic_filter" in tool.description

    def test_inherits_base_params(self):
        kf = KnowledgeFilterExpr(operator="eq", field="s", value="a")
        tool = AgenticLangchainKnowledgeSearchTool(
            rag=_mock_rag(),
            top_k=8,
            search_type=SearchType.MAX_MARGINAL_RELEVANCE,
            min_score=0.3,
            knowledge_filter=kf,
        )
        assert tool.top_k == 8
        assert tool.search_type == SearchType.MAX_MARGINAL_RELEVANCE
        assert tool.min_score == 0.3
        assert tool.knowledge_filter is kf


# ---------------------------------------------------------------------------
# AgenticLangchainKnowledgeSearchTool._run_async_impl
# ---------------------------------------------------------------------------


class TestAgenticRunAsyncImpl:
    @pytest.mark.asyncio
    async def test_no_dynamic_filter_uses_static(self):
        result = _search_result_with_docs(("static", 0.8))
        rag = _mock_rag(result)
        kf = KnowledgeFilterExpr(operator="eq", field="status", value="active")
        tool = AgenticLangchainKnowledgeSearchTool(rag=rag, knowledge_filter=kf)
        inv_ctx = _mock_invocation_context(create_agent_context())
        docs = await tool._run_async_impl(
            tool_context=inv_ctx, args={"query": "q"}
        )
        rag.build_search_extra_params.assert_called_once_with(kf)

    @pytest.mark.asyncio
    async def test_dynamic_filter_only(self):
        result = _search_result_with_docs(("dynamic", 0.7))
        rag = _mock_rag(result)
        tool = AgenticLangchainKnowledgeSearchTool(rag=rag, knowledge_filter=None)
        inv_ctx = _mock_invocation_context(create_agent_context())
        dynamic = {"operator": "eq", "field": "cat", "value": "ml"}
        docs = await tool._run_async_impl(
            tool_context=inv_ctx, args={"query": "q", "dynamic_filter": dynamic}
        )
        call_filter = rag.build_search_extra_params.call_args[0][0]
        assert isinstance(call_filter, KnowledgeFilterExpr)
        assert call_filter.operator == "eq"
        assert call_filter.field == "cat"
        assert call_filter.value == "ml"

    @pytest.mark.asyncio
    async def test_both_static_and_dynamic_combined_as_and(self):
        result = _search_result_with_docs(("combined", 0.6))
        rag = _mock_rag(result)
        static_kf = KnowledgeFilterExpr(operator="eq", field="status", value="active")
        tool = AgenticLangchainKnowledgeSearchTool(rag=rag, knowledge_filter=static_kf)
        inv_ctx = _mock_invocation_context(create_agent_context())
        dynamic = {"operator": "eq", "field": "cat", "value": "ml"}
        docs = await tool._run_async_impl(
            tool_context=inv_ctx, args={"query": "q", "dynamic_filter": dynamic}
        )
        call_filter = rag.build_search_extra_params.call_args[0][0]
        assert call_filter.operator == "and"
        assert len(call_filter.value) == 2
        assert call_filter.value[0] is static_kf
        assert call_filter.value[1].operator == "eq"
        assert call_filter.value[1].field == "cat"

    @pytest.mark.asyncio
    async def test_dynamic_filter_none_no_static(self):
        result = SearchResult(documents=[])
        rag = _mock_rag(result)
        tool = AgenticLangchainKnowledgeSearchTool(rag=rag, knowledge_filter=None)
        inv_ctx = _mock_invocation_context(create_agent_context())
        docs = await tool._run_async_impl(
            tool_context=inv_ctx, args={"query": "q", "dynamic_filter": None}
        )
        rag.build_search_extra_params.assert_called_once_with(None)

    @pytest.mark.asyncio
    async def test_dynamic_filter_not_provided_in_args(self):
        result = SearchResult(documents=[])
        rag = _mock_rag(result)
        kf = KnowledgeFilterExpr(operator="eq", field="x", value="y")
        tool = AgenticLangchainKnowledgeSearchTool(rag=rag, knowledge_filter=kf)
        inv_ctx = _mock_invocation_context(create_agent_context())
        docs = await tool._run_async_impl(
            tool_context=inv_ctx, args={"query": "q"}
        )
        rag.build_search_extra_params.assert_called_once_with(kf)


# ---------------------------------------------------------------------------
# Module exports via tools/__init__.py
# ---------------------------------------------------------------------------


class TestToolsModuleExports:
    def test_all_exports(self):
        from trpc_agent_sdk.server.knowledge.tools import (
            AgenticLangchainKnowledgeSearchTool,
            LangchainKnowledgeSearchTool,
        )

        assert AgenticLangchainKnowledgeSearchTool is not None
        assert LangchainKnowledgeSearchTool is not None

    def test_all_list(self):
        import trpc_agent_sdk.server.knowledge.tools as tools_mod

        expected = {"AgenticLangchainKnowledgeSearchTool", "LangchainKnowledgeSearchTool"}
        assert set(tools_mod.__all__) == expected
