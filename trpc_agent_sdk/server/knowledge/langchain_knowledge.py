# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""This module integrates with the Langchain ecosystem, providing a default implementation of RAG."""
from enum import Enum
from typing import Any
from typing import Dict
from typing import List

from pydantic import BaseModel

from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import BaseDocumentTransformer
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.messages import BaseMessage
from langchain_core.prompt_values import PromptValue
from langchain_core.prompts.base import BasePromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import RunnableConfig
from langchain_core.vectorstores import VectorStore

# Version compatibility: Support both LangChain 0.3.x and 1.x.x
# In LangChain 1.x, Chain is deprecated in favor of Runnable
try:
    from langchain_core.runnables import Runnable as Chain
except ImportError:
    # Fallback to Chain for LangChain 0.3.x
    from langchain.chains.base import Chain

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.knowledge import (
    KnowledgeBase,
    KnowledgeFilterExpr,
    SearchDocument,
    SearchRequest,
    SearchResult,
)
from trpc_agent_sdk.log import logger
from typing_extensions import override


# SearchType vector retrieval type
class SearchType(Enum):
    SIMILARITY_SCORE_THRESHOLD = "similarity_score_threshold"
    SIMILARITY = "similarity"
    MAX_MARGINAL_RELEVANCE = "mmr"


# Return relevant documents format type
class ListType(Enum):
    TYPE_LIST_DOCUMENT = 0
    TYPE_LIST_DOCUMENT_SCORE = 1


class LangchainParams(BaseModel):
    """Langchain chain kwargs parameters, user can define"""
    # TODO: add Langchain chain kwargs parameters


class LangchainKnowledge(KnowledgeBase):

    def __init__(self,
                 chain: Chain = None,
                 prompt_template: BasePromptTemplate = None,
                 document_loader: BaseLoader = None,
                 document_transformer: BaseDocumentTransformer = None,
                 embedder: Embeddings = None,
                 vectorstore: VectorStore = None,
                 retriever: BaseRetriever = None):
        """Implement the default logic for RAG, integrate with tRPC-Agent framework, support Langchain ecosystem

        :params:
        chain: complete Langchain chain; if a complete Langchain chain is already available, it can be directly called, other configurations are ignored
        prompt_template: Langchain Prompt template
        document_loader: Langchain document loader
        document_transformer: Langchain document transformer
        embedder: Langchain vector embedding model
        vectorstore: Langchain vector database
        retriever: Langchain retriever
        """
        # Initialize embedder etc. components, if chain is not empty, other configurations will be ignored and Chain will be called directly
        self.chain = chain
        self.prompt_template = prompt_template
        self.document_loader = document_loader
        # document_transformer if None, default is not split documents
        self.document_transformer = document_transformer
        self.embedder = embedder
        self.vectorstore = vectorstore
        self.retriever = retriever
        self.common_runnable_config = None

    def _check_chain_component(self):
        """Check the necessary components for RAG."""
        if self.vectorstore is None and self.retriever is None:
            raise TypeError("vectorstore and retriever is None, "
                            "vectorstore or retriever should be initialized.")

    def _parse_agent_config(self, ctx: AgentContext) -> RunnableConfig:
        """Parse agent configuration and return stream mode and runnable config.

        :param ctx: agent context
        :return: runnable config
        """
        runnable_config = ctx.get_metadata("runnable_config")

        return runnable_config

    def _check_list_type(self, relevant_documents) -> bool:
        """Check if the returned relevant documents contain relevance scores"""
        if len(relevant_documents) == 0:
            return ListType.TYPE_LIST_DOCUMENT.value
        if isinstance(relevant_documents, list) and isinstance(relevant_documents[0], tuple):
            return ListType.TYPE_LIST_DOCUMENT_SCORE.value
        return ListType.TYPE_LIST_DOCUMENT.value

    def _tran_result(self, relevant_documents) -> SearchResult:
        """Convert the relevant documents retrieved to SearchResult"""
        search_results: SearchResult = SearchResult()

        is_contains_score: bool = (self._check_list_type(relevant_documents) == ListType.TYPE_LIST_DOCUMENT_SCORE.value)

        for doc in relevant_documents:
            search_document: SearchDocument = SearchDocument()
            if is_contains_score:
                search_document.document = doc[0]
                search_document.score = doc[1]
            else:
                search_document.document = doc
            search_results.documents.append(search_document)

        return search_results

    def _get_history_message(self, ctx: AgentContext, req: SearchRequest) -> str:
        """Get the history conversation context"""
        history: List[BaseMessage] = req.history
        user_id: str = req.user_id
        session_id: str = req.session_id
        assistant_name: str = ctx.get_metadata("assistant_name")

        context: str = ""
        context += f"user_id: {user_id}\n"
        if assistant_name:
            context += f"assistant: {assistant_name}\n"
        context += f"session_id: {session_id}\n"
        context += f"content: "
        for msg in history:
            context += msg.text()

        return context

    def gen_langchain_extra_params(self, langchain_params: LangchainParams) -> Dict:
        """Generate Langchain extra parameters

        :param langchain_params: Langchain parameters
        :return: Langchain extra parameters
        """
        return {"langchain": langchain_params.model_dump()}

    async def _run_chain(self, context: str, req: SearchRequest, kwargs):
        """Run the complete Langchain chain directly"""
        if req.query.text:
            query = req.query.text
        else:
            raise ValueError(f"query should be text, but got None")

        chain_runnable_config = None
        if self.common_runnable_config:
            chain_runnable_config = self.common_runnable_config.copy()

        if 'chain_runnable_config' in req.params.extra_params:
            chain_runnable_config = req.params.extra_params.get('chain_runnable_config')

        relevant_documents = await self.chain.ainvoke({
            "context": context,
            "query": query
        }, chain_runnable_config, **kwargs)

        return relevant_documents

    async def _run_vectorstore_retrieve(self, req: SearchRequest, query: str, langchain_kwargs):
        """Use the vector database for retrieval"""
        search_type: str = req.params.search_type
        if search_type == SearchType.SIMILARITY_SCORE_THRESHOLD.value:
            # query with Score
            return await self.vectorstore.asimilarity_search_with_relevance_scores(query=query,
                                                                                   k=req.params.rank_top_k,
                                                                                   **langchain_kwargs)
        # query without Score
        return await self.vectorstore.asearch(
            query=query,
            search_type=search_type,
            k=req.params.rank_top_k,
            **langchain_kwargs,
        )

    async def _run_retriever_retrieve(self, req: SearchRequest, query: str, langchain_kwargs):
        """Use the retriever for retrieval"""
        retriever_runnable_config = None
        if self.common_runnable_config:
            retriever_runnable_config = self.common_runnable_config.copy()

        if 'retriever_runnable_config' in req.params.extra_params:
            retriever_runnable_config: RunnableConfig = req.params.extra_params.get('retriever_runnable_config')

        relevant_documents: List[Document] = await self.retriever.ainvoke(input=query,
                                                                          config=retriever_runnable_config,
                                                                          **langchain_kwargs)

        return relevant_documents

    async def _run_vectorstore_retriever_retrieve(self, req: SearchRequest, query: str, langchain_kwargs):
        """Use the vector database for retrieval, and use the retriever for reranking"""
        relevant_documents = await self._run_vectorstore_retrieve(req=req,
                                                                  query=query,
                                                                  langchain_kwargs=langchain_kwargs)

        retriever = self.retriever.from_documents(relevant_documents)

        retriever_runnable_config = None
        if self.common_runnable_config:
            retriever_runnable_config = self.common_runnable_config.copy()
        if 'retriever_runnable_config' in req.params.extra_params:
            retriever_runnable_config: RunnableConfig = req.params.extra_params.get('retriever_runnable_config')

        relevant_documents: List[Document] = await retriever.ainvoke(input=query,
                                                                     config=retriever_runnable_config,
                                                                     **langchain_kwargs)

        return relevant_documents

    @override
    async def search(self, ctx: AgentContext, req: SearchRequest) -> SearchResult:
        """Implement the default logic for RAG"""
        self.common_runnable_config: RunnableConfig = self._parse_agent_config(ctx)

        # Get the previous conversation context
        context = self._get_history_message(ctx=ctx, req=req)

        # Extra parameters
        langchain_kwargs = LangchainParams().model_dump()
        langchain_kwargs.update(req.params.extra_params.get('langchain', {}))

        if self.chain:
            # If a complete Langchain chain is already available, call the chain directly
            relevant_documents = await self._run_chain(context=context, req=req, kwargs=langchain_kwargs)
            return self._tran_result(relevant_documents)

        # If there is no complete Langchain chain, check the necessary components for RAG
        self._check_chain_component()

        # Build the Prompt
        if req.query.text:
            query = req.query.text
        else:
            raise ValueError(f"query should be text, but got None")

        if self.prompt_template:
            query_runnable_config = None
            if self.common_runnable_config:
                query_runnable_config = self.common_runnable_config.copy()
            if 'query_runnable_config' in req.params.extra_params:
                query_runnable_config = req.params.extra_params.get('query_runnable_config')

            query: PromptValue = await self.prompt_template.ainvoke({
                "context": context,
                "query": query
            }, query_runnable_config, **langchain_kwargs)
            query: str = query.to_string()

        # If the retriever is not specified, use the vector database for retrieval
        if self.vectorstore and self.retriever is None:
            relevant_documents = await self._run_vectorstore_retrieve(req=req,
                                                                      query=query,
                                                                      langchain_kwargs=langchain_kwargs)
        elif self.vectorstore is None and self.retriever:
            # If the vector database is not specified, use the retriever for retrieval
            relevant_documents = await self._run_retriever_retrieve(req=req,
                                                                    query=query,
                                                                    langchain_kwargs=langchain_kwargs)
        elif self.vectorstore and self.retriever:
            # If both are specified, use the vector database for retrieval, and use the retriever for reranking
            relevant_documents = await self._run_vectorstore_retriever_retrieve(req=req,
                                                                                query=query,
                                                                                langchain_kwargs=langchain_kwargs)

        # Adapt to the tRPC-Agent framework SearchResult
        search_results: SearchResult = self._tran_result(relevant_documents=relevant_documents)

        return search_results

    async def create_vectorstore_from_document(
        self,
        **kwargs: Any,
    ) -> None:
        """Create the vector database from documents"""
        # Read the documents
        documents = []
        if self.document_loader:
            documents = await self.document_loader.aload()
        else:
            logger.info("document loader is None")

        # Split the documents
        if self.document_transformer:
            documents = await self.document_transformer.atransform_documents(documents, **kwargs)
        else:
            logger.info("document transformer is None")

        # Build the vector database
        vector_db = await self.vectorstore.afrom_documents(documents, embedding=self.embedder, **kwargs)
        self.vectorstore = vector_db
