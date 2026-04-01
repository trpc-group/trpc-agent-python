# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from typing import Any
from typing import Iterable
from typing import List

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.prompts import PromptTemplate
from langchain_core.retrievers import BaseRetriever
from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.knowledge import SearchRequest
from trpc_agent_sdk.knowledge import SearchResult
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge
from trpc_agent_sdk.types import Part


class ToyRetriever(BaseRetriever):
    """A toy retriever that contains the top k documents that contain the user query.

    This retriever only implements the sync method _get_relevant_documents.

    If the retriever were to involve file access or network access, it could benefit
    from a native async implementation of `_aget_relevant_documents`.

    As usual, with Runnables, there's a default async implementation that's provided
    that delegates to the sync implementation running on another thread.
    """

    documents: List[Document]
    """List of documents to retrieve from."""
    k: int
    """Number of top results to return"""

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> List[Document]:
        """Sync implementations for retriever."""
        matching_documents = []
        for document in self.documents:
            if len(matching_documents) >= self.k:
                return matching_documents

            if query.lower() in document.page_content.lower():
                matching_documents.append(document)
        return matching_documents

    # Optional: Provide a more efficient native implementation by overriding
    # _aget_relevant_documents
    # async def _aget_relevant_documents(
    #     self, query: str, *, run_manager: AsyncCallbackManagerForRetrieverRun
    # ) -> List[Document]:
    #     """Asynchronously get documents relevant to a query.

    #     Args:
    #         query: String to find relevant documents for
    #         run_manager: The callbacks handler to use

    #     Returns:
    #         List of relevant documents
    #     """

    # Optional: If you want to use retriever with vectorstore together in LangChainKnowledge,
    #           you should implement this method
    @classmethod
    def from_documents(
        cls,
        documents: Iterable[Document],
        **kwargs: Any,
    ) -> "ToyRetriever":
        """
        Create a ToyRetriever from a list of Documents.
        Args:
            documents: A list of Documents to vectorize.
            **kwargs: Any other arguments to pass to the retriever.

        Returns:
            A ToyRetriever instance.
        """
        # Extract k parameter from kwargs, default to 3
        k = kwargs.pop('k', 3)

        # Convert documents to list if it's an iterable
        doc_list = list(documents)

        # Create and return ToyRetriever instance
        return cls(documents=doc_list, k=k, **kwargs)


def build_chain():
    template = "{query}"
    prompt = PromptTemplate.from_template(template)
    test_documents = [
        Document(page_content="Shenzhen: sunny", metadata={"source": "weather.txt"}),
        Document(page_content="Shanghai: cloud", metadata={"source": "weather.txt"})
    ]
    retriever = ToyRetriever.from_documents(test_documents, k=1)
    rag = LangchainKnowledge(
        prompt_template=prompt,
        retriever=retriever,
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
    query = "Shenzhen"
    res = await simple_search(query)
    print(res)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
