# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio

from dotenv import load_dotenv
from trpc_agent.context import new_agent_context
from trpc_agent.knowledge import SearchRequest
from trpc_agent.knowledge import SearchResult
from trpc_agent.types import Part

# Load environment variables from the .env file
load_dotenv()


async def simple_search(rag, query: str):
    """Search using a LangchainKnowledge instance."""
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


async def run_knowledge_demo():
    """Run the knowledge custom components demo."""

    from agent.agent import (
        create_document_loader_knowledge,
        create_text_splitter_knowledge,
        create_retriever_knowledge,
    )
    from agent.config import TEST_DATA_FILE, TEST_DATA_CONTENT

    # 生成测试文件
    with open(TEST_DATA_FILE, "w", encoding="utf-8") as file:
        file.write(TEST_DATA_CONTENT)

    # Demo 1: Custom Document Loader
    print("=" * 50)
    print("Demo 1: Custom Document Loader")
    print("=" * 50)

    rag_loader = create_document_loader_knowledge()
    # 从文档创建向量数据库
    await rag_loader.create_vectorstore_from_document()

    query = "beijing"
    print(f"📝 Query: {query}")
    res = await simple_search(rag_loader, query)
    print(f"🤖 Result: {res}")
    print("-" * 40)

    # Demo 2: Custom Text Splitter
    print("=" * 50)
    print("Demo 2: Custom Text Splitter")
    print("=" * 50)

    rag_splitter = create_text_splitter_knowledge()
    # 从文档创建向量数据库
    await rag_splitter.create_vectorstore_from_document()

    query = "beijing"
    print(f"📝 Query: {query}")
    res = await simple_search(rag_splitter, query)
    print(f"🤖 Result: {res}")
    print("-" * 40)

    # Demo 3: Custom Retriever
    print("=" * 50)
    print("Demo 3: Custom Retriever")
    print("=" * 50)

    rag_retriever = create_retriever_knowledge()

    query = "Shenzhen"
    print(f"📝 Query: {query}")
    res = await simple_search(rag_retriever, query)
    print(f"🤖 Result: {res}")
    print("-" * 40)


if __name__ == "__main__":
    asyncio.run(run_knowledge_demo())
