# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
""" Custom components for LangchainKnowledge. """

import asyncio
from typing import Any, AsyncIterator, Iterable, Iterator, List, Sequence

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import BaseDocumentTransformer, Document
from langchain_core.retrievers import BaseRetriever


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
