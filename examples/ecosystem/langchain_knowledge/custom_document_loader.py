# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
from typing import AsyncIterator
from typing import Iterator

# Compatible imports for LangChain 0.3.x and 1.x.x
try:
    # langchain v1.x.x版本导入方式
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    # langchain v0.3.x版本导入方式
    from langchain.text_splitter import RecursiveCharacterTextSplitter

from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_huggingface import HuggingFaceEmbeddings
from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.knowledge import SearchRequest, SearchResult
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge


class CustomDocumentLoader(BaseLoader):
    """An example document loader that reads a file line by line."""

    def __init__(self, file_path: str) -> None:
        """Initialize the loader with a file path.

        Args:
            file_path: The path to the file to load.
        """
        self.file_path = file_path

    def lazy_load(self) -> Iterator[Document]:  # <-- Does not take any arguments
        """A lazy loader that reads a file line by line.

        When you're implementing lazy load methods, you should use a generator
        to yield documents one by one.
        """
        with open(self.file_path, encoding="utf-8") as f:
            line_number = 0
            for line in f:
                yield Document(
                    page_content=line,
                    metadata={
                        "line_number": line_number,
                        "source": self.file_path
                    },
                )
                line_number += 1

    # alazy_load is OPTIONAL.
    # If you leave out the implementation, a default implementation which delegates to lazy_load will be used!
    async def alazy_load(self) -> AsyncIterator[Document]:  # <-- Does not take any arguments
        # """An async lazy loader that reads a file line by line."""
        try:
            # Requires aiofiles
            # https://github.com/Tinche/aiofiles
            import aiofiles

            async with aiofiles.open(self.file_path, encoding="utf-8") as f:
                line_number = 0
                async for line in f:
                    yield Document(
                        page_content=line,
                        metadata={
                            "line_number": line_number,
                            "source": self.file_path
                        },
                    )
                    line_number += 1
        except ImportError:
            # Fallback to super class implementation if aiofiles is not available
            async for item in super().alazy_load():
                yield item


def build_chain():
    template = """Answer the question gently:
    Query: {query}
    """
    prompt = ChatPromptTemplate.from_template(template)
    embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    vectorstore = InMemoryVectorStore(embedder)
    text_loader = CustomDocumentLoader("test.data")
    # 这里由于测试文本较短，所以chunk_size设置为10，实际使用时需要根据文本长度调整
    text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)

    rag = LangchainKnowledge(
        prompt_template=prompt,
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=embedder,
        vectorstore=vectorstore,
    )
    return rag


rag = build_chain()


async def simple_search(query: str):
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


async def main():
    # 生成测试文件
    with open("test.data", "w", encoding="utf-8") as file:
        file.write("shenzhen: sunny\nbeijing: cloudy\nhangzhou: rainy")

    # 从文档创建向量数据库
    await rag.create_vectorstore_from_document()

    # 检索
    query = "beijing"
    res = await simple_search(query)
    print(res)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
