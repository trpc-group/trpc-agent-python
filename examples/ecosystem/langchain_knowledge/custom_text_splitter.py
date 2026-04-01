# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
from typing import Any
from typing import Sequence

from langchain_community.document_loaders import TextLoader
from langchain_core.documents import BaseDocumentTransformer
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_huggingface import HuggingFaceEmbeddings
from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.knowledge import SearchRequest
from trpc_agent_sdk.knowledge import SearchResult
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge
from trpc_agent_sdk.types import Part


class CustomTextSplitter(BaseDocumentTransformer):
    """Interface for splitting text into chunks."""

    def __init__(self, separator: str) -> None:
        """Create a new TextSplitter."""
        self.separator = separator

    def transform_documents(self, documents: Sequence[Document], **kwargs: Any) -> Sequence[Document]:
        """Transform a list of documents.

        Args:
            documents: A sequence of Documents to be transformed.

        Returns:
            A sequence of transformed Documents.
        """
        transformed_docs = []

        for doc in documents:
            # Split the document content by separator
            text_chunks = doc.page_content.split(self.separator)

            # Create new documents for each chunk
            for i, chunk in enumerate(text_chunks):
                # Skip empty chunks
                if chunk.strip():
                    # Create new document with the chunk content
                    new_doc = Document(
                        page_content=chunk.strip(),
                        metadata={
                            **doc.metadata,  # Preserve original metadata
                            "chunk_index": i,  # Add chunk index
                            "original_doc_id": id(doc),  # Reference to original document
                        })
                    transformed_docs.append(new_doc)

        return transformed_docs

    async def atransform_documents(self, documents: Sequence[Document], **kwargs: Any) -> Sequence[Document]:
        """Asynchronously transform a list of documents.

        Args:
            documents: A sequence of Documents to be transformed.

        Returns:
            A sequence of transformed Documents.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.transform_documents, documents, **kwargs)


def build_chain():
    template = """Answer the question gently:
    Query: {query}
    """
    prompt = ChatPromptTemplate.from_template(template)
    embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    vectorstore = InMemoryVectorStore(embedder)
    text_loader = TextLoader("test.data")
    text_splitter = CustomTextSplitter(separator="\n")

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
        "assistant_name": "test",  # Agent Name, 可用于上下文
        "runnable_config": {},  # Langchain中的Runnable配置
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
