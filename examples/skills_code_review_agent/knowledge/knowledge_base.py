# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Knowledge base for the code review agent.

Provides RAG (Retrieval-Augmented Generation) capability by indexing
coding standards and best practices documents. The Agent can use this
knowledge base to reference coding standards during code review.

Usage:
    from knowledge import knowledge_search_tool
    agent = LlmAgent(..., tools=[knowledge_search_tool, ...])
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

from trpc_agent_sdk.knowledge import SearchRequest, SearchResult
from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.types import Part

# Lazy imports for LangChain — only resolve when actually used
_langchain_available = False
LangchainKnowledge = None
TextLoader = None
RecursiveCharacterTextSplitter = None
InMemoryVectorStore = None
HuggingFaceEmbeddings = None

try:
    from langchain_community.document_loaders import TextLoader as _TextLoader
    from langchain_core.vectorstores import InMemoryVectorStore as _InMemoryVectorStore
    from langchain_huggingface import HuggingFaceEmbeddings as _HuggingFaceEmbeddings

    try:
        from langchain.text_splitter import RecursiveCharacterTextSplitter as _RCTS
    except ModuleNotFoundError:
        from langchain_text_splitters import RecursiveCharacterTextSplitter as _RCTS

    from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge as _LK

    TextLoader = _TextLoader
    RecursiveCharacterTextSplitter = _RCTS
    InMemoryVectorStore = _InMemoryVectorStore
    HuggingFaceEmbeddings = _HuggingFaceEmbeddings
    LangchainKnowledge = _LK
    _langchain_available = True
except ImportError:
    pass


def _get_coding_standards_path() -> str:
    """Get the path to the coding standards document."""
    return str(Path(__file__).parent / "coding_standards.md")


def build_knowledge():
    """Build the RAG knowledge base from coding standards documents.

    Returns:
        LangchainKnowledge instance if LangChain is available, None otherwise.
    """
    if not _langchain_available:
        return None

    # Read the coding standards document
    doc_path = _get_coding_standards_path()
    text_loader = TextLoader(doc_path, encoding="utf-8")

    # Split into chunks
    text_splitter = RecursiveCharacterTextSplitter(
        separators=["\n## ", "\n### ", "\n\n", "\n", " "],
        chunk_size=500,
        chunk_overlap=50,
    )

    # Use in-memory vector store with lightweight embeddings
    embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    vectorstore = InMemoryVectorStore(embedder)

    # Build the knowledge base
    from trpc_agent_sdk.knowledge._filter_expr import KnowledgeFilterExpr

    rag = LangchainKnowledge(
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=embedder,
        vectorstore=vectorstore,
        search_type="similarity",
        search_kwargs={"k": 3},
    )
    return rag


# Global knowledge instance
_knowledge = None


def get_knowledge():
    """Get or create the knowledge base singleton."""
    global _knowledge
    if _knowledge is None:
        _knowledge = build_knowledge()
    return _knowledge


async def knowledge_search(query: str) -> dict:
    """Search the coding standards knowledge base.

    Args:
        query: The search query (e.g. "SQL injection prevention", "async resource cleanup").

    Returns:
        A dict with search results or an error message.
    """
    rag = get_knowledge()
    if rag is None:
        return {
            "status": "unavailable",
            "message": "Knowledge base not available. Install langchain dependencies: "
                       "pip install trpc-agent-py[knowledge]",
        }

    try:
        ctx = new_agent_context(timeout=5000)
        search_req = SearchRequest()
        search_req.query = Part.from_text(text=query)
        search_result: SearchResult = await rag.search(ctx, search_req)

        if not search_result.documents:
            return {"status": "success", "results": [], "message": "No matching standards found."}

        results = []
        for doc in search_result.documents:
            results.append({
                "content": doc.document.page_content,
                "score": doc.score,
                "source": doc.document.metadata.get("source", ""),
            })

        return {
            "status": "success",
            "results": results,
            "count": len(results),
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Knowledge search failed: {type(e).__name__}: {str(e)}",
        }


# Create the FunctionTool
from trpc_agent_sdk.tools import FunctionTool
knowledge_search_tool = FunctionTool(knowledge_search)