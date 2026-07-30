# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""This module provides the main knowledge management interface for trpc-agent."""

from abc import ABC
from abc import abstractmethod
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from langchain_core.documents import Document
from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.types import Part

from ._filter_expr import KnowledgeFilterExpr


# SearchParams contains parameters related to search, user can customize
class SearchParams(BaseModel):
    # Search type: support similarity, similarity_score_threshold, mmr
    search_type: str = "similarity"
    # TopP set probability cumulative threshold (optional, default value)
    top_p: float = 0.8
    # rank_top_k return the number of the most related K results (optional, default value)
    rank_top_k: int = 3
    # rerank_threshold minimum relevance score threshold for reranking (optional, default value)
    rerank_threshold: float = 0.3
    # default_score relevance score (optional, default value)
    default_score: float = 0.0
    # generator_temperature temperature parameter for the generator model (optional, default value)
    generator_temperature: float = 0.0
    # generator_max_tokens maximum number of tokens output by the generator model (optional, default value)
    generator_max_tokens: int = 5000
    # extra_params used to store implementation-specific parameters (user-defined)
    extra_params: Optional[Dict[str, Any]] = {}


# SearchRequest represents a search request with context
class SearchRequest(BaseModel):
    # query is the content submitted for search
    query: Part = None

    # history contains the most recent conversation messages as context.
    history: List[BaseMessage] = []

    # user_id can be used for personalized search results
    user_id: str = ""

    # session_id can be used for session-specific context
    session_id: str = ""

    # params user can customize parameters related to search
    params: SearchParams = SearchParams()


# SearchDocument represents a single document searched with relevance score
class SearchDocument(BaseModel):
    # document is the single document matched
    document: Document = None
    # relevance score
    score: float = 0.0


# SearchResult represents the result of knowledge search
class SearchResult(BaseModel):
    # documents are the multiple documents matched
    documents: List[SearchDocument] = []


class KnowledgeBase(ABC):

    @abstractmethod
    async def search(self, ctx: AgentContext, req: SearchRequest) -> SearchResult:
        """
        Execute semantic search and return the best result, this is the main method for Agent to use RAG.

        :param ctx: context, contains conversation history for better understanding.
        :param req: request object, contains query and context information.
        :return SearchResult: search result.
        """

    def build_search_extra_params(self, filter_expr: Optional[KnowledgeFilterExpr]) -> Dict[str, Any]:
        """Build backend-specific extra parameters from a unified filter expression."""
        return {}
