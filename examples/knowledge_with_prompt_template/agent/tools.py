# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
Tools for the agent.

本模块负责构建 RAG 知识库及 Agent 可调用的 SearchTool。
支持根据不同的 Prompt Template 类型（PromptTemplate / ChatPromptTemplate / MessagesPlaceholder）
构建对应的 LangchainKnowledge 实例。
"""

import atexit
import os
import tempfile

from langchain_community.document_loaders import TextLoader
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_huggingface import HuggingFaceEmbeddings

# 兼容 LangChain 0.3.x 和 1.x.x 的导入方式
try:
    from langchain.text_splitter import RecursiveCharacterTextSplitter
except ModuleNotFoundError:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

from trpc_agent_sdk.server.knowledge.langchain_knowledge import (
    LangchainKnowledge,
    SearchType,
)
from trpc_agent_sdk.server.knowledge.tools import LangchainKnowledgeSearchTool

from .prompts import PROMPT_TEMPLATES

# The original text of the knowledge base for demonstration, can be replaced with external files or data sources when used in practice
TEXT_CONTENT = ("人工智能（Artificial Intelligence，简称AI）是计算机科学的一个分支，"
                "它企图了解智能的实质，并生产出一种新的能以人类智能相似的方式做出反应的智能机器。"
                "人工智能的研究领域包括机器学习、自然语言处理、计算机视觉、专家系统等。"
                "深度学习是机器学习的一个子领域，它使用多层神经网络来学习数据的表示。")

_temp_files: list[str] = []


def _cleanup_temp_files():
    for path in _temp_files:
        try:
            os.unlink(path)
        except OSError:
            pass


atexit.register(_cleanup_temp_files)


def _create_text_loader() -> TextLoader:
    """Write the text content to a temporary file and create TextLoader.

    TextLoader needs to be loaded from the file path, so the content is written to a temporary file first.
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8") as tmp_file:
        tmp_file.write(TEXT_CONTENT)
    _temp_files.append(tmp_file.name)
    return TextLoader(tmp_file.name, encoding="utf-8")


def build_knowledge(prompt_template_name: str) -> LangchainKnowledge:
    """Build the RAG knowledge base according to the specified Prompt Template type.

    Args:
        prompt_template_name: Template name, possible values:
            - "string_prompt":  PromptTemplate (StringPromptTemplate)
            - "chat_prompt":    ChatPromptTemplate
            - "messages_prompt": ChatPromptTemplate with MessagesPlaceholder

    Returns:
        The built LangchainKnowledge instance (need to call create_vectorstore_from_document to search)
    """
    prompt_template = PROMPT_TEMPLATES.get(prompt_template_name)
    if prompt_template is None:
        raise ValueError(f"Unsupported prompt_template_name: {prompt_template_name!r},"
                         f"possible values: {', '.join(PROMPT_TEMPLATES.keys())}")

    # Embedder: Use the HuggingFace bge-small-en-v1.5 model to generate vectors
    embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    # Use the memory vector store, suitable for demonstration scenarios
    vectorstore = InMemoryVectorStore(embedder)
    # Load the document
    text_loader = _create_text_loader()
    # Text split: chunk_size=10 because the demonstration text is short, the actual usage should be adjusted according to the text length
    text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)

    # Assemble the RAG pipeline: prompt_template determines how the search result is formatted and passed to LLM
    rag = LangchainKnowledge(
        prompt_template=prompt_template,
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=embedder,
        vectorstore=vectorstore,
    )
    return rag


def build_search_tool(prompt_template_name: str) -> tuple[LangchainKnowledge, LangchainKnowledgeSearchTool]:
    """Build the knowledge base and the corresponding SearchTool, for Agent to use as a tool.

    Args:
        prompt_template_name: Template name, same as build_knowledge

    Returns:
        (rag, search_tool) tuple, rag is used to initialize the vector store, search_tool is passed to Agent
    """
    rag = build_knowledge(prompt_template_name)
    # top_k=1 returns the most relevant 1 document, using vector similarity search
    search_tool = LangchainKnowledgeSearchTool(rag, top_k=1, search_type=SearchType.SIMILARITY)
    return rag, search_tool
