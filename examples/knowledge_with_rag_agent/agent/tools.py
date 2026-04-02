# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
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

from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.knowledge import SearchRequest, SearchResult
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge

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
    # 使用 TextLoader：将文本写入临时文件后加载
    text_content = ("人工智能（Artificial Intelligence，简称AI）是计算机科学的一个分支，"
                    "它企图了解智能的实质，并生产出一种新的能以人类智能相似的方式做出反应的智能机器。")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8") as tmp_file:
        tmp_file.write(text_content)
    _temp_files.append(tmp_file.name)
    text_loader = TextLoader(tmp_file.name, encoding="utf-8")
    # 这里由于测试文本较短，所以chunk_size设置为10，实际使用时需要根据文本长度调整
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


async def simple_search(query: str):
    """Search the knowledge base for relevant documents"""
    # metadata 可用于存储元数据
    metadata = {
        'assistant_name': 'test',  # Agent Name, 可用于上下文
        'runnable_config': {},  # Langchain中的Runnable配置
    }
    ctx = new_agent_context(timeout=3000, metadata=metadata)
    sr: SearchRequest = SearchRequest()
    sr.query = Part.from_text(text=query)
    search_result: SearchResult = await rag.search(ctx, sr)
    if len(search_result.documents) == 0:
        return {"status": "failed", "report": "No documents found"}

    best_doc = search_result.documents[0].document
    return {"status": "success", "report": f"content: {best_doc.page_content}"}
