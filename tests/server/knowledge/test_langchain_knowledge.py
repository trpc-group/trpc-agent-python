# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for trpc_agent_sdk.server.knowledge.langchain_knowledge module."""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage

from trpc_agent_sdk.context import AgentContext, create_agent_context
from trpc_agent_sdk.knowledge import (
    SearchDocument,
    SearchParams,
    SearchRequest,
    SearchResult,
)
from trpc_agent_sdk.server.knowledge.langchain_knowledge import (
    LangchainKnowledge,
    LangchainParams,
    ListType,
    SearchType,
)
from trpc_agent_sdk.types import Part


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(**metadata) -> AgentContext:
    ctx = create_agent_context()
    for k, v in metadata.items():
        ctx.with_metadata(k, v)
    return ctx


def _make_request(
    query_text: str = "test query",
    search_type: str = "similarity",
    rank_top_k: int = 3,
    extra_params: Dict[str, Any] | None = None,
    history: list | None = None,
    user_id: str = "u1",
    session_id: str = "s1",
) -> SearchRequest:
    params = SearchParams(
        search_type=search_type,
        rank_top_k=rank_top_k,
        extra_params=extra_params if extra_params is not None else {},
    )
    req = SearchRequest(
        query=Part.from_text(text=query_text),
        params=params,
        user_id=user_id,
        session_id=session_id,
        history=history or [],
    )
    return req


# ---------------------------------------------------------------------------
# SearchType enum
# ---------------------------------------------------------------------------


class TestSearchType:
    def test_similarity_score_threshold(self):
        assert SearchType.SIMILARITY_SCORE_THRESHOLD.value == "similarity_score_threshold"

    def test_similarity(self):
        assert SearchType.SIMILARITY.value == "similarity"

    def test_mmr(self):
        assert SearchType.MAX_MARGINAL_RELEVANCE.value == "mmr"

    def test_all_members(self):
        assert len(SearchType) == 3


# ---------------------------------------------------------------------------
# ListType enum
# ---------------------------------------------------------------------------


class TestListType:
    def test_document_type(self):
        assert ListType.TYPE_LIST_DOCUMENT.value == 0

    def test_document_score_type(self):
        assert ListType.TYPE_LIST_DOCUMENT_SCORE.value == 1


# ---------------------------------------------------------------------------
# LangchainParams
# ---------------------------------------------------------------------------


class TestLangchainParams:
    def test_default_construction(self):
        params = LangchainParams()
        assert params.model_dump() == {}

    def test_model_dump_empty(self):
        params = LangchainParams()
        dumped = params.model_dump()
        assert isinstance(dumped, dict)


# ---------------------------------------------------------------------------
# LangchainKnowledge.__init__
# ---------------------------------------------------------------------------


class TestLangchainKnowledgeInit:
    def test_defaults(self):
        lk = LangchainKnowledge()
        assert lk.chain is None
        assert lk.prompt_template is None
        assert lk.document_loader is None
        assert lk.document_transformer is None
        assert lk.embedder is None
        assert lk.vectorstore is None
        assert lk.retriever is None
        assert lk.common_runnable_config is None

    def test_with_chain(self):
        mock_chain = MagicMock()
        lk = LangchainKnowledge(chain=mock_chain)
        assert lk.chain is mock_chain

    def test_with_all_components(self):
        chain = MagicMock()
        prompt = MagicMock()
        loader = MagicMock()
        transformer = MagicMock()
        embedder = MagicMock()
        vs = MagicMock()
        retriever = MagicMock()
        lk = LangchainKnowledge(
            chain=chain,
            prompt_template=prompt,
            document_loader=loader,
            document_transformer=transformer,
            embedder=embedder,
            vectorstore=vs,
            retriever=retriever,
        )
        assert lk.chain is chain
        assert lk.prompt_template is prompt
        assert lk.document_loader is loader
        assert lk.document_transformer is transformer
        assert lk.embedder is embedder
        assert lk.vectorstore is vs
        assert lk.retriever is retriever


# ---------------------------------------------------------------------------
# _check_chain_component
# ---------------------------------------------------------------------------


class TestCheckChainComponent:
    def test_raises_when_both_none(self):
        lk = LangchainKnowledge()
        with pytest.raises(TypeError, match="vectorstore and retriever is None"):
            lk._check_chain_component()

    def test_ok_with_vectorstore(self):
        lk = LangchainKnowledge(vectorstore=MagicMock())
        lk._check_chain_component()

    def test_ok_with_retriever(self):
        lk = LangchainKnowledge(retriever=MagicMock())
        lk._check_chain_component()

    def test_ok_with_both(self):
        lk = LangchainKnowledge(vectorstore=MagicMock(), retriever=MagicMock())
        lk._check_chain_component()


# ---------------------------------------------------------------------------
# _parse_agent_config
# ---------------------------------------------------------------------------


class TestParseAgentConfig:
    def test_returns_runnable_config_from_metadata(self):
        ctx = _make_ctx(runnable_config={"key": "val"})
        lk = LangchainKnowledge()
        result = lk._parse_agent_config(ctx)
        assert result == {"key": "val"}

    def test_returns_none_when_no_metadata(self):
        ctx = create_agent_context()
        lk = LangchainKnowledge()
        result = lk._parse_agent_config(ctx)
        assert result is None


# ---------------------------------------------------------------------------
# _check_list_type
# ---------------------------------------------------------------------------


class TestCheckListType:
    def test_empty_list(self):
        lk = LangchainKnowledge()
        assert lk._check_list_type([]) == ListType.TYPE_LIST_DOCUMENT.value

    def test_list_of_documents(self):
        lk = LangchainKnowledge()
        docs = [Document(page_content="a"), Document(page_content="b")]
        assert lk._check_list_type(docs) == ListType.TYPE_LIST_DOCUMENT.value

    def test_list_of_tuples(self):
        lk = LangchainKnowledge()
        docs = [(Document(page_content="a"), 0.9), (Document(page_content="b"), 0.8)]
        assert lk._check_list_type(docs) == ListType.TYPE_LIST_DOCUMENT_SCORE.value


# ---------------------------------------------------------------------------
# _tran_result
# ---------------------------------------------------------------------------


class TestTranResult:
    def test_empty_list(self):
        lk = LangchainKnowledge()
        result = lk._tran_result([])
        assert isinstance(result, SearchResult)
        assert len(result.documents) == 0

    def test_documents_without_score(self):
        lk = LangchainKnowledge()
        docs = [Document(page_content="hello"), Document(page_content="world")]
        result = lk._tran_result(docs)
        assert len(result.documents) == 2
        assert result.documents[0].document.page_content == "hello"
        assert result.documents[0].score == 0.0
        assert result.documents[1].document.page_content == "world"

    def test_documents_with_score(self):
        lk = LangchainKnowledge()
        docs = [
            (Document(page_content="a"), 0.95),
            (Document(page_content="b"), 0.80),
        ]
        result = lk._tran_result(docs)
        assert len(result.documents) == 2
        assert result.documents[0].score == 0.95
        assert result.documents[0].document.page_content == "a"
        assert result.documents[1].score == 0.80

    def test_single_document_without_score(self):
        lk = LangchainKnowledge()
        docs = [Document(page_content="only")]
        result = lk._tran_result(docs)
        assert len(result.documents) == 1
        assert result.documents[0].document.page_content == "only"

    def test_single_document_with_score(self):
        lk = LangchainKnowledge()
        docs = [(Document(page_content="scored"), 0.5)]
        result = lk._tran_result(docs)
        assert len(result.documents) == 1
        assert result.documents[0].score == 0.5


# ---------------------------------------------------------------------------
# _get_history_message
# ---------------------------------------------------------------------------


class TestGetHistoryMessage:
    def test_basic_context(self):
        ctx = _make_ctx(assistant_name="bot")
        history = [HumanMessage(content="hi"), AIMessage(content="hello")]
        req = _make_request(history=history, user_id="user1", session_id="sess1")
        lk = LangchainKnowledge()
        msg = lk._get_history_message(ctx, req)
        assert "user_id: user1" in msg
        assert "assistant: bot" in msg
        assert "session_id: sess1" in msg

    def test_no_assistant_name(self):
        ctx = create_agent_context()
        req = _make_request(history=[], user_id="u", session_id="s")
        lk = LangchainKnowledge()
        msg = lk._get_history_message(ctx, req)
        assert "user_id: u" in msg
        assert "assistant:" not in msg
        assert "session_id: s" in msg

    def test_empty_history(self):
        ctx = _make_ctx(assistant_name="bot")
        req = _make_request(history=[], user_id="u1", session_id="s1")
        lk = LangchainKnowledge()
        msg = lk._get_history_message(ctx, req)
        assert "content:" in msg


# ---------------------------------------------------------------------------
# gen_langchain_extra_params
# ---------------------------------------------------------------------------


class TestGenLangchainExtraParams:
    def test_returns_dict_with_langchain_key(self):
        lk = LangchainKnowledge()
        params = LangchainParams()
        result = lk.gen_langchain_extra_params(params)
        assert "langchain" in result
        assert isinstance(result["langchain"], dict)


# ---------------------------------------------------------------------------
# _run_chain
# ---------------------------------------------------------------------------


class TestRunChain:
    @pytest.mark.asyncio
    async def test_basic_chain_call(self):
        mock_chain = AsyncMock()
        mock_chain.ainvoke.return_value = [Document(page_content="chain result")]
        lk = LangchainKnowledge(chain=mock_chain)
        req = _make_request(extra_params={})
        result = await lk._run_chain("context text", req, {})
        mock_chain.ainvoke.assert_awaited_once()
        call_args = mock_chain.ainvoke.call_args
        assert call_args[0][0]["query"] == "test query"
        assert call_args[0][0]["context"] == "context text"

    @pytest.mark.asyncio
    async def test_chain_with_common_runnable_config(self):
        mock_chain = AsyncMock()
        mock_chain.ainvoke.return_value = []
        lk = LangchainKnowledge(chain=mock_chain)
        lk.common_runnable_config = {"configurable": {"key": "val"}}
        req = _make_request(extra_params={})
        await lk._run_chain("ctx", req, {})
        call_args = mock_chain.ainvoke.call_args
        assert call_args[0][1] == {"configurable": {"key": "val"}}

    @pytest.mark.asyncio
    async def test_chain_with_request_runnable_config(self):
        mock_chain = AsyncMock()
        mock_chain.ainvoke.return_value = []
        lk = LangchainKnowledge(chain=mock_chain)
        lk.common_runnable_config = {"old": True}
        req = _make_request(extra_params={"chain_runnable_config": {"new": True}})
        await lk._run_chain("ctx", req, {})
        call_args = mock_chain.ainvoke.call_args
        assert call_args[0][1] == {"new": True}

    @pytest.mark.asyncio
    async def test_chain_raises_when_no_text(self):
        mock_chain = AsyncMock()
        lk = LangchainKnowledge(chain=mock_chain)
        req = SearchRequest(params=SearchParams(extra_params={}))
        req.query = Part()
        with pytest.raises(ValueError, match="query should be text"):
            await lk._run_chain("ctx", req, {})


# ---------------------------------------------------------------------------
# _run_vectorstore_retrieve
# ---------------------------------------------------------------------------


class TestRunVectorstoreRetrieve:
    @pytest.mark.asyncio
    async def test_similarity_score_threshold(self):
        mock_vs = AsyncMock()
        mock_vs.asimilarity_search_with_relevance_scores.return_value = [
            (Document(page_content="doc"), 0.9)
        ]
        lk = LangchainKnowledge(vectorstore=mock_vs)
        req = _make_request(search_type="similarity_score_threshold", rank_top_k=5)
        result = await lk._run_vectorstore_retrieve(req, "query text", {})
        mock_vs.asimilarity_search_with_relevance_scores.assert_awaited_once_with(
            query="query text", k=5
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_similarity_search(self):
        mock_vs = AsyncMock()
        mock_vs.asearch.return_value = [Document(page_content="doc")]
        lk = LangchainKnowledge(vectorstore=mock_vs)
        req = _make_request(search_type="similarity", rank_top_k=3)
        result = await lk._run_vectorstore_retrieve(req, "query", {})
        mock_vs.asearch.assert_awaited_once_with(
            query="query", search_type="similarity", k=3
        )

    @pytest.mark.asyncio
    async def test_mmr_search(self):
        mock_vs = AsyncMock()
        mock_vs.asearch.return_value = []
        lk = LangchainKnowledge(vectorstore=mock_vs)
        req = _make_request(search_type="mmr", rank_top_k=2)
        await lk._run_vectorstore_retrieve(req, "q", {})
        mock_vs.asearch.assert_awaited_once_with(
            query="q", search_type="mmr", k=2
        )

    @pytest.mark.asyncio
    async def test_with_extra_kwargs(self):
        mock_vs = AsyncMock()
        mock_vs.asearch.return_value = []
        lk = LangchainKnowledge(vectorstore=mock_vs)
        req = _make_request(search_type="similarity")
        await lk._run_vectorstore_retrieve(req, "q", {"filter": {"k": "v"}})
        call_kwargs = mock_vs.asearch.call_args[1]
        assert call_kwargs["filter"] == {"k": "v"}


# ---------------------------------------------------------------------------
# _run_retriever_retrieve
# ---------------------------------------------------------------------------


class TestRunRetrieverRetrieve:
    @pytest.mark.asyncio
    async def test_basic_retriever(self):
        mock_retriever = AsyncMock()
        mock_retriever.ainvoke.return_value = [Document(page_content="ret")]
        lk = LangchainKnowledge(retriever=mock_retriever)
        req = _make_request(extra_params={})
        result = await lk._run_retriever_retrieve(req, "query", {})
        mock_retriever.ainvoke.assert_awaited_once()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_retriever_with_common_config(self):
        mock_retriever = AsyncMock()
        mock_retriever.ainvoke.return_value = []
        lk = LangchainKnowledge(retriever=mock_retriever)
        lk.common_runnable_config = {"cfg": True}
        req = _make_request(extra_params={})
        await lk._run_retriever_retrieve(req, "q", {})
        call_kwargs = mock_retriever.ainvoke.call_args[1]
        assert call_kwargs["config"] == {"cfg": True}

    @pytest.mark.asyncio
    async def test_retriever_with_request_config(self):
        mock_retriever = AsyncMock()
        mock_retriever.ainvoke.return_value = []
        lk = LangchainKnowledge(retriever=mock_retriever)
        lk.common_runnable_config = {"old": True}
        req = _make_request(extra_params={"retriever_runnable_config": {"new": True}})
        await lk._run_retriever_retrieve(req, "q", {})
        call_kwargs = mock_retriever.ainvoke.call_args[1]
        assert call_kwargs["config"] == {"new": True}


# ---------------------------------------------------------------------------
# _run_vectorstore_retriever_retrieve
# ---------------------------------------------------------------------------


class TestRunVectorstoreRetrieverRetrieve:
    @pytest.mark.asyncio
    async def test_vectorstore_then_retriever(self):
        mock_vs = AsyncMock()
        vs_docs = [Document(page_content="vs_doc")]
        mock_vs.asearch.return_value = vs_docs

        mock_retriever = MagicMock()
        reranked_retriever = AsyncMock()
        reranked_retriever.ainvoke.return_value = [Document(page_content="reranked")]
        mock_retriever.from_documents.return_value = reranked_retriever

        lk = LangchainKnowledge(vectorstore=mock_vs, retriever=mock_retriever)
        req = _make_request(search_type="similarity", extra_params={})
        result = await lk._run_vectorstore_retriever_retrieve(req, "q", {})
        mock_vs.asearch.assert_awaited_once()
        mock_retriever.from_documents.assert_called_once_with(vs_docs)
        assert len(result) == 1
        assert result[0].page_content == "reranked"

    @pytest.mark.asyncio
    async def test_with_retriever_config_override(self):
        mock_vs = AsyncMock()
        mock_vs.asearch.return_value = []
        mock_retriever = MagicMock()
        reranked_retriever = AsyncMock()
        reranked_retriever.ainvoke.return_value = []
        mock_retriever.from_documents.return_value = reranked_retriever

        lk = LangchainKnowledge(vectorstore=mock_vs, retriever=mock_retriever)
        lk.common_runnable_config = {"old": True}
        req = _make_request(extra_params={"retriever_runnable_config": {"custom": True}})
        await lk._run_vectorstore_retriever_retrieve(req, "q", {})
        call_kwargs = reranked_retriever.ainvoke.call_args[1]
        assert call_kwargs["config"] == {"custom": True}

    @pytest.mark.asyncio
    async def test_with_common_config(self):
        mock_vs = AsyncMock()
        mock_vs.asearch.return_value = []
        mock_retriever = MagicMock()
        reranked_retriever = AsyncMock()
        reranked_retriever.ainvoke.return_value = []
        mock_retriever.from_documents.return_value = reranked_retriever

        lk = LangchainKnowledge(vectorstore=mock_vs, retriever=mock_retriever)
        lk.common_runnable_config = {"common": True}
        req = _make_request(extra_params={})
        await lk._run_vectorstore_retriever_retrieve(req, "q", {})
        call_kwargs = reranked_retriever.ainvoke.call_args[1]
        assert call_kwargs["config"] == {"common": True}


# ---------------------------------------------------------------------------
# search (integration-level, various paths)
# ---------------------------------------------------------------------------


class TestSearch:
    @pytest.mark.asyncio
    async def test_search_with_chain(self):
        mock_chain = AsyncMock()
        mock_chain.ainvoke.return_value = [Document(page_content="chain_doc")]
        lk = LangchainKnowledge(chain=mock_chain)
        ctx = _make_ctx()
        req = _make_request()
        result = await lk.search(ctx, req)
        assert isinstance(result, SearchResult)
        assert len(result.documents) == 1
        assert result.documents[0].document.page_content == "chain_doc"

    @pytest.mark.asyncio
    async def test_search_with_chain_scored(self):
        mock_chain = AsyncMock()
        mock_chain.ainvoke.return_value = [
            (Document(page_content="scored"), 0.88),
        ]
        lk = LangchainKnowledge(chain=mock_chain)
        ctx = _make_ctx()
        req = _make_request()
        result = await lk.search(ctx, req)
        assert result.documents[0].score == 0.88

    @pytest.mark.asyncio
    async def test_search_vectorstore_only(self):
        mock_vs = AsyncMock()
        mock_vs.asearch.return_value = [Document(page_content="vs_doc")]
        lk = LangchainKnowledge(vectorstore=mock_vs)
        ctx = _make_ctx()
        req = _make_request()
        result = await lk.search(ctx, req)
        assert len(result.documents) == 1
        assert result.documents[0].document.page_content == "vs_doc"

    @pytest.mark.asyncio
    async def test_search_retriever_only(self):
        mock_retriever = AsyncMock()
        mock_retriever.ainvoke.return_value = [Document(page_content="ret_doc")]
        lk = LangchainKnowledge(retriever=mock_retriever)
        ctx = _make_ctx()
        req = _make_request()
        result = await lk.search(ctx, req)
        assert len(result.documents) == 1
        assert result.documents[0].document.page_content == "ret_doc"

    @pytest.mark.asyncio
    async def test_search_vectorstore_and_retriever(self):
        mock_vs = AsyncMock()
        mock_vs.asearch.return_value = [Document(page_content="vs")]
        mock_retriever = MagicMock()
        reranked = AsyncMock()
        reranked.ainvoke.return_value = [Document(page_content="reranked")]
        mock_retriever.from_documents.return_value = reranked

        lk = LangchainKnowledge(vectorstore=mock_vs, retriever=mock_retriever)
        ctx = _make_ctx()
        req = _make_request()
        result = await lk.search(ctx, req)
        assert len(result.documents) == 1
        assert result.documents[0].document.page_content == "reranked"

    @pytest.mark.asyncio
    async def test_search_raises_when_no_components(self):
        lk = LangchainKnowledge()
        ctx = _make_ctx()
        req = _make_request()
        with pytest.raises(TypeError, match="vectorstore and retriever is None"):
            await lk.search(ctx, req)

    @pytest.mark.asyncio
    async def test_search_raises_when_query_is_none(self):
        lk = LangchainKnowledge(vectorstore=MagicMock())
        ctx = _make_ctx()
        req = SearchRequest(params=SearchParams(extra_params={}))
        req.query = Part()
        with pytest.raises(ValueError, match="query should be text"):
            await lk.search(ctx, req)

    @pytest.mark.asyncio
    async def test_search_with_prompt_template_vectorstore(self):
        mock_vs = AsyncMock()
        mock_vs.asearch.return_value = [Document(page_content="doc")]

        mock_prompt_value = MagicMock()
        mock_prompt_value.to_string.return_value = "transformed query"
        mock_prompt = AsyncMock()
        mock_prompt.ainvoke.return_value = mock_prompt_value

        lk = LangchainKnowledge(vectorstore=mock_vs, prompt_template=mock_prompt)
        ctx = _make_ctx()
        req = _make_request()
        result = await lk.search(ctx, req)
        mock_prompt.ainvoke.assert_awaited_once()
        mock_vs.asearch.assert_awaited_once()
        assert "transformed query" in mock_vs.asearch.call_args[1]["query"]

    @pytest.mark.asyncio
    async def test_search_with_prompt_template_and_query_runnable_config(self):
        mock_vs = AsyncMock()
        mock_vs.asearch.return_value = []

        mock_prompt_value = MagicMock()
        mock_prompt_value.to_string.return_value = "q"
        mock_prompt = AsyncMock()
        mock_prompt.ainvoke.return_value = mock_prompt_value

        lk = LangchainKnowledge(vectorstore=mock_vs, prompt_template=mock_prompt)
        ctx = _make_ctx(runnable_config={"base": True})
        req = _make_request(extra_params={"query_runnable_config": {"override": True}})
        await lk.search(ctx, req)
        call_args = mock_prompt.ainvoke.call_args
        assert call_args[0][1] == {"override": True}

    @pytest.mark.asyncio
    async def test_search_with_prompt_common_config(self):
        mock_vs = AsyncMock()
        mock_vs.asearch.return_value = []

        mock_prompt_value = MagicMock()
        mock_prompt_value.to_string.return_value = "q"
        mock_prompt = AsyncMock()
        mock_prompt.ainvoke.return_value = mock_prompt_value

        lk = LangchainKnowledge(vectorstore=mock_vs, prompt_template=mock_prompt)
        ctx = _make_ctx(runnable_config={"common": True})
        req = _make_request(extra_params={})
        await lk.search(ctx, req)
        call_args = mock_prompt.ainvoke.call_args
        assert call_args[0][1] == {"common": True}

    @pytest.mark.asyncio
    async def test_search_uses_langchain_extra_params(self):
        mock_vs = AsyncMock()
        mock_vs.asearch.return_value = []
        lk = LangchainKnowledge(vectorstore=mock_vs)
        ctx = _make_ctx()
        req = _make_request(extra_params={"langchain": {"custom_flag": True}})
        await lk.search(ctx, req)
        call_kwargs = mock_vs.asearch.call_args[1]
        assert call_kwargs.get("custom_flag") is True

    @pytest.mark.asyncio
    async def test_search_similarity_score_threshold_path(self):
        mock_vs = AsyncMock()
        mock_vs.asimilarity_search_with_relevance_scores.return_value = [
            (Document(page_content="scored"), 0.95),
        ]
        lk = LangchainKnowledge(vectorstore=mock_vs)
        ctx = _make_ctx()
        req = _make_request(search_type="similarity_score_threshold")
        result = await lk.search(ctx, req)
        assert len(result.documents) == 1
        assert result.documents[0].score == 0.95


# ---------------------------------------------------------------------------
# create_vectorstore_from_document
# ---------------------------------------------------------------------------


class TestCreateVectorstoreFromDocument:
    @pytest.mark.asyncio
    async def test_full_pipeline(self):
        mock_loader = AsyncMock()
        raw_docs = [Document(page_content="raw")]
        mock_loader.aload.return_value = raw_docs

        mock_transformer = AsyncMock()
        split_docs = [Document(page_content="split1"), Document(page_content="split2")]
        mock_transformer.atransform_documents.return_value = split_docs

        mock_embedder = MagicMock()
        mock_vs = AsyncMock()
        new_vs = MagicMock()
        mock_vs.afrom_documents.return_value = new_vs

        lk = LangchainKnowledge(
            document_loader=mock_loader,
            document_transformer=mock_transformer,
            embedder=mock_embedder,
            vectorstore=mock_vs,
        )
        await lk.create_vectorstore_from_document()
        mock_loader.aload.assert_awaited_once()
        mock_transformer.atransform_documents.assert_awaited_once_with(raw_docs)
        mock_vs.afrom_documents.assert_awaited_once_with(
            split_docs, embedding=mock_embedder
        )
        assert lk.vectorstore is new_vs

    @pytest.mark.asyncio
    async def test_no_loader(self):
        mock_vs = AsyncMock()
        mock_vs.afrom_documents.return_value = MagicMock()
        lk = LangchainKnowledge(vectorstore=mock_vs, embedder=MagicMock())
        await lk.create_vectorstore_from_document()
        mock_vs.afrom_documents.assert_awaited_once()
        call_args = mock_vs.afrom_documents.call_args
        assert call_args[0][0] == []

    @pytest.mark.asyncio
    async def test_no_transformer(self):
        mock_loader = AsyncMock()
        raw_docs = [Document(page_content="raw")]
        mock_loader.aload.return_value = raw_docs

        mock_vs = AsyncMock()
        mock_vs.afrom_documents.return_value = MagicMock()

        lk = LangchainKnowledge(
            document_loader=mock_loader,
            vectorstore=mock_vs,
            embedder=MagicMock(),
        )
        await lk.create_vectorstore_from_document()
        call_args = mock_vs.afrom_documents.call_args
        assert call_args[0][0] == raw_docs

    @pytest.mark.asyncio
    async def test_with_kwargs(self):
        mock_vs = AsyncMock()
        mock_vs.afrom_documents.return_value = MagicMock()
        lk = LangchainKnowledge(vectorstore=mock_vs, embedder=MagicMock())
        await lk.create_vectorstore_from_document(collection_name="test")
        call_kwargs = mock_vs.afrom_documents.call_args[1]
        assert call_kwargs["collection_name"] == "test"
