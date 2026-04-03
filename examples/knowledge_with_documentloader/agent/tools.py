# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Tools for the agent. """

import atexit
import os
import tempfile

from langchain_community.document_loaders import PyPDFLoader
from langchain_community.document_loaders import TextLoader
from langchain_community.document_loaders import UnstructuredMarkdownLoader
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_huggingface import HuggingFaceEmbeddings

try:
    from langchain.text_splitter import RecursiveCharacterTextSplitter
except ModuleNotFoundError:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.knowledge import SearchRequest, SearchResult
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge

from .prompts import rag_prompt

LOADER_TYPE = os.getenv("DOCUMENT_LOADER_TYPE", "text")
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-small-en-v1.5")

_temp_files: list[str] = []


def _cleanup_temp_files():
    for path in _temp_files:
        try:
            os.unlink(path)
        except OSError:
            pass


atexit.register(_cleanup_temp_files)


def _create_text_loader():
    """Use TextLoader to load pure text files"""
    text_content = ("Artificial Intelligence (AI) is a branch of computer science, "
                    "It attempts to understand the essence of intelligence and produce a "
                    "new intelligent machine that can react in a way similar to human intelligence.")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8") as tmp_file:
        tmp_file.write(text_content)
    _temp_files.append(tmp_file.name)
    return TextLoader(tmp_file.name, encoding="utf-8")


def _create_pypdf_loader():
    """Use PyPDFLoader to load PDF files"""
    pdf_path = os.getenv("DOCUMENT_PDF_PATH", "")
    if not pdf_path:
        raise ValueError("Use PyPDFLoader to load PDF files, need to set environment "
                         "variable DOCUMENT_PDF_PATH to the PDF file path")
    return PyPDFLoader(pdf_path)


def _create_unstructured_markdown_loader():
    """Use UnstructuredMarkdownLoader to load Markdown files"""
    md_content = ("# Introduction to Artificial Intelligence\n\n"
                  "Artificial Intelligence (AI) is a branch of computer science, "
                  "It attempts to understand the essence of intelligence and produce a "
                  "new intelligent machine that can react in a way similar to human intelligence.\n\n"
                  "## Main research fields\n\n"
                  "- Machine learning\n"
                  "- Natural language processing\n"
                  "- Computer vision\n")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".md", mode="w", encoding="utf-8") as tmp_file:
        tmp_file.write(md_content)
    _temp_files.append(tmp_file.name)
    return UnstructuredMarkdownLoader(tmp_file.name, mode="single", strategy="fast")


LOADER_FACTORY = {
    "text": _create_text_loader,
    "pdf": _create_pypdf_loader,
    "markdown": _create_unstructured_markdown_loader,
}


def build_knowledge():
    """Build the RAG knowledge chain with the specified DocumentLoader.

    Args:
        None

    Returns:
        LangchainKnowledge object, used to search the knowledge base
    """
    if LOADER_TYPE not in LOADER_FACTORY:
        raise ValueError(f"Unsupported DOCUMENT_LOADER_TYPE: {LOADER_TYPE},"
                         f"Supported values: {', '.join(LOADER_FACTORY.keys())}")

    loader = LOADER_FACTORY[LOADER_TYPE]()
    embedder = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_NAME)
    vectorstore = InMemoryVectorStore(embedder)
    text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)

    rag = LangchainKnowledge(
        prompt_template=rag_prompt,
        document_loader=loader,
        document_transformer=text_splitter,
        embedder=embedder,
        vectorstore=vectorstore,
    )
    return rag


rag = build_knowledge()


async def simple_search(query: str):
    """Search the knowledge base for relevant documents.

    Args:
        query: Search query

    Returns:
        Dictionary containing search result
    """
    metadata = {
        'assistant_name': 'test',
        'runnable_config': {},
    }
    ctx = new_agent_context(timeout=3000, metadata=metadata)
    sr: SearchRequest = SearchRequest()
    sr.query = Part.from_text(text=query)
    search_result: SearchResult = await rag.search(ctx, sr)
    if len(search_result.documents) == 0:
        return {"status": "failed", "report": "No documents found"}

    best_doc = search_result.documents[0].document
    return {"status": "success", "report": f"content: {best_doc.page_content}"}
