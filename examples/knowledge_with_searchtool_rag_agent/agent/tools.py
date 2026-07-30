# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Tools for the agent. """

import atexit
import os
import tempfile

from langchain_community.document_loaders import TextLoader
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_huggingface import HuggingFaceEmbeddings

try:
    from langchain.text_splitter import RecursiveCharacterTextSplitter
except ModuleNotFoundError:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

from trpc_agent_sdk.server.knowledge.langchain_knowledge import (
    LangchainKnowledge,
    SearchType,
)
from trpc_agent_sdk.server.knowledge.tools import LangchainKnowledgeSearchTool

from .prompts import rag_prompt

_temp_files: list[str] = []


def _cleanup_temp_files():
    for path in _temp_files:
        try:
            os.unlink(path)
        except OSError:
            pass


atexit.register(_cleanup_temp_files)


def build_knowledge():
    """Build the RAG knowledge chain"""
    embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    vectorstore = InMemoryVectorStore(embedder)

    text_content = ("人工智能（Artificial Intelligence，简称AI）是计算机科学的一个分支，"
                    "它企图了解智能的实质，并生产出一种新的能以人类智能相似的方式做出反应的智能机器。")
    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8")
    tmp_file.write(text_content)
    tmp_file.flush()
    tmp_file.close()
    _temp_files.append(tmp_file.name)
    text_loader = TextLoader(tmp_file.name, encoding="utf-8")
    # chunk_size设置为10是因为测试文本较短，实际使用时需要根据文本长度调整
    text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)

    rag = LangchainKnowledge(
        prompt_template=rag_prompt,
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=embedder,
        vectorstore=vectorstore,
    )
    return rag


rag = build_knowledge()

search_tool = LangchainKnowledgeSearchTool(rag, top_k=1, search_type=SearchType.SIMILARITY)
