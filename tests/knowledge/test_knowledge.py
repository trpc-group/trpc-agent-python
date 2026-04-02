# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for knowledge models (SearchParams, SearchRequest, SearchDocument, SearchResult, KnowledgeBase)."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage

from trpc_agent_sdk.context import AgentContext, create_agent_context
from trpc_agent_sdk.knowledge._filter_expr import KnowledgeFilterExpr
from trpc_agent_sdk.knowledge._knowledge import (
    KnowledgeBase,
    SearchDocument,
    SearchParams,
    SearchRequest,
    SearchResult,
)
from trpc_agent_sdk.types import Part


# ---------------------------------------------------------------------------
# SearchParams
# ---------------------------------------------------------------------------


class TestSearchParams:
    def test_default_values(self):
        params = SearchParams()
        assert params.search_type == "similarity"
        assert params.top_p == 0.8
        assert params.rank_top_k == 3
        assert params.rerank_threshold == 0.3
        assert params.default_score == 0.0
        assert params.generator_temperature == 0.0
        assert params.generator_max_tokens == 5000
        assert params.extra_params == {}

    def test_custom_values(self):
        params = SearchParams(
            search_type="mmr",
            top_p=0.9,
            rank_top_k=5,
            rerank_threshold=0.5,
            default_score=0.1,
            generator_temperature=0.7,
            generator_max_tokens=2000,
            extra_params={"custom_key": "custom_val"},
        )
        assert params.search_type == "mmr"
        assert params.top_p == 0.9
        assert params.rank_top_k == 5
        assert params.rerank_threshold == 0.5
        assert params.default_score == 0.1
        assert params.generator_temperature == 0.7
        assert params.generator_max_tokens == 2000
        assert params.extra_params == {"custom_key": "custom_val"}

    def test_extra_params_none(self):
        params = SearchParams(extra_params=None)
        assert params.extra_params is None

    def test_search_type_similarity_score_threshold(self):
        params = SearchParams(search_type="similarity_score_threshold")
        assert params.search_type == "similarity_score_threshold"

    def test_serialization_round_trip(self):
        params = SearchParams(search_type="mmr", top_p=0.95, rank_top_k=10)
        data = params.model_dump()
        restored = SearchParams.model_validate(data)
        assert restored.search_type == params.search_type
        assert restored.top_p == params.top_p
        assert restored.rank_top_k == params.rank_top_k


# ---------------------------------------------------------------------------
# SearchRequest
# ---------------------------------------------------------------------------


class TestSearchRequest:
    def test_default_values(self):
        req = SearchRequest()
        assert req.query is None
        assert req.history == []
        assert req.user_id == ""
        assert req.session_id == ""
        assert isinstance(req.params, SearchParams)

    def test_with_query_part(self):
        part = Part(text="what is AI?")
        req = SearchRequest(query=part)
        assert req.query.text == "what is AI?"

    def test_with_history(self):
        messages = [
            HumanMessage(content="Hello"),
            AIMessage(content="Hi there"),
        ]
        req = SearchRequest(history=messages)
        assert len(req.history) == 2
        assert req.history[0].content == "Hello"
        assert req.history[1].content == "Hi there"

    def test_with_user_and_session_ids(self):
        req = SearchRequest(user_id="user-123", session_id="sess-456")
        assert req.user_id == "user-123"
        assert req.session_id == "sess-456"

    def test_with_custom_params(self):
        params = SearchParams(search_type="mmr", rank_top_k=10)
        req = SearchRequest(params=params)
        assert req.params.search_type == "mmr"
        assert req.params.rank_top_k == 10

    def test_full_construction(self):
        req = SearchRequest(
            query=Part(text="search query"),
            history=[HumanMessage(content="context")],
            user_id="u1",
            session_id="s1",
            params=SearchParams(top_p=0.5),
        )
        assert req.query.text == "search query"
        assert len(req.history) == 1
        assert req.user_id == "u1"
        assert req.session_id == "s1"
        assert req.params.top_p == 0.5


# ---------------------------------------------------------------------------
# SearchDocument
# ---------------------------------------------------------------------------


class TestSearchDocument:
    def test_default_values(self):
        doc = SearchDocument()
        assert doc.document is None
        assert doc.score == 0.0

    def test_with_document_and_score(self):
        lc_doc = Document(page_content="hello world", metadata={"source": "test"})
        doc = SearchDocument(document=lc_doc, score=0.85)
        assert doc.document.page_content == "hello world"
        assert doc.document.metadata == {"source": "test"}
        assert doc.score == 0.85

    def test_zero_score(self):
        lc_doc = Document(page_content="low relevance")
        doc = SearchDocument(document=lc_doc, score=0.0)
        assert doc.score == 0.0

    def test_high_score(self):
        lc_doc = Document(page_content="exact match")
        doc = SearchDocument(document=lc_doc, score=1.0)
        assert doc.score == 1.0

    def test_negative_score(self):
        lc_doc = Document(page_content="negative")
        doc = SearchDocument(document=lc_doc, score=-0.5)
        assert doc.score == -0.5


# ---------------------------------------------------------------------------
# SearchResult
# ---------------------------------------------------------------------------


class TestSearchResult:
    def test_default_empty_documents(self):
        result = SearchResult()
        assert result.documents == []

    def test_with_documents(self):
        docs = [
            SearchDocument(document=Document(page_content="doc1"), score=0.9),
            SearchDocument(document=Document(page_content="doc2"), score=0.7),
        ]
        result = SearchResult(documents=docs)
        assert len(result.documents) == 2
        assert result.documents[0].score == 0.9
        assert result.documents[1].document.page_content == "doc2"

    def test_single_document(self):
        result = SearchResult(
            documents=[SearchDocument(document=Document(page_content="only"), score=0.5)]
        )
        assert len(result.documents) == 1

    def test_serialization_round_trip(self):
        docs = [
            SearchDocument(document=Document(page_content="test", metadata={"k": "v"}), score=0.8),
        ]
        result = SearchResult(documents=docs)
        data = result.model_dump()
        restored = SearchResult.model_validate(data)
        assert len(restored.documents) == 1
        assert restored.documents[0].score == 0.8


# ---------------------------------------------------------------------------
# KnowledgeBase (abstract)
# ---------------------------------------------------------------------------


class ConcreteKnowledgeBase(KnowledgeBase):
    """Concrete implementation for testing the abstract class."""

    async def search(self, ctx: AgentContext, req: SearchRequest) -> SearchResult:
        return SearchResult(
            documents=[
                SearchDocument(
                    document=Document(page_content=f"result for: {req.query.text}"),
                    score=0.99,
                )
            ]
        )


class ConcreteWithExtraParams(KnowledgeBase):
    """Concrete implementation that overrides build_search_extra_params."""

    async def search(self, ctx: AgentContext, req: SearchRequest) -> SearchResult:
        return SearchResult()

    def build_search_extra_params(self, filter_expr: Optional[KnowledgeFilterExpr]) -> Dict[str, Any]:
        if filter_expr is None:
            return {}
        return {"filter": filter_expr.model_dump()}


class TestKnowledgeBase:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            KnowledgeBase()

    def test_concrete_can_instantiate(self):
        kb = ConcreteKnowledgeBase()
        assert isinstance(kb, KnowledgeBase)

    def test_search_returns_result(self):
        kb = ConcreteKnowledgeBase()
        ctx = create_agent_context()
        req = SearchRequest(query=Part(text="hello"))

        async def run():
            return await kb.search(ctx, req)

        result = asyncio.run(run())
        assert isinstance(result, SearchResult)
        assert len(result.documents) == 1
        assert "hello" in result.documents[0].document.page_content
        assert result.documents[0].score == 0.99

    def test_build_search_extra_params_default_returns_empty(self):
        kb = ConcreteKnowledgeBase()
        assert kb.build_search_extra_params(None) == {}
        expr = KnowledgeFilterExpr(operator="eq", field="status", value="active")
        assert kb.build_search_extra_params(expr) == {}

    def test_build_search_extra_params_override(self):
        kb = ConcreteWithExtraParams()
        expr = KnowledgeFilterExpr(operator="eq", field="status", value="active")
        result = kb.build_search_extra_params(expr)
        assert "filter" in result
        assert result["filter"]["operator"] == "eq"
        assert result["filter"]["field"] == "status"
        assert result["filter"]["value"] == "active"

    def test_build_search_extra_params_override_none(self):
        kb = ConcreteWithExtraParams()
        assert kb.build_search_extra_params(None) == {}

    def test_search_with_full_request(self):
        kb = ConcreteKnowledgeBase()
        ctx = create_agent_context()
        req = SearchRequest(
            query=Part(text="deep learning"),
            history=[HumanMessage(content="I'm studying ML")],
            user_id="user1",
            session_id="s1",
            params=SearchParams(search_type="mmr", rank_top_k=5),
        )

        async def run():
            return await kb.search(ctx, req)

        result = asyncio.run(run())
        assert len(result.documents) == 1
        assert "deep learning" in result.documents[0].document.page_content

    def test_search_with_agent_context(self):
        kb = ConcreteKnowledgeBase()
        ctx = create_agent_context()
        ctx.set_timeout(5000)
        ctx.with_metadata("key", "value")
        req = SearchRequest(query=Part(text="test"))

        async def run():
            return await kb.search(ctx, req)

        result = asyncio.run(run())
        assert isinstance(result, SearchResult)


# ---------------------------------------------------------------------------
# Module exports via __init__.py
# ---------------------------------------------------------------------------


class TestModuleExports:
    def test_all_exports_available(self):
        from trpc_agent_sdk.knowledge import (
            KnowledgeBase,
            KnowledgeFilterExpr,
            SearchDocument,
            SearchParams,
            SearchRequest,
            SearchResult,
        )

        assert KnowledgeBase is not None
        assert KnowledgeFilterExpr is not None
        assert SearchDocument is not None
        assert SearchParams is not None
        assert SearchRequest is not None
        assert SearchResult is not None

    def test_all_list(self):
        import trpc_agent_sdk.knowledge as knowledge_mod

        expected = {
            "KnowledgeFilterExpr",
            "KnowledgeBase",
            "SearchDocument",
            "SearchParams",
            "SearchRequest",
            "SearchResult",
        }
        assert set(knowledge_mod.__all__) == expected
