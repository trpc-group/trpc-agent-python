# Custom Langchain RAG Components

This document describes how to customize the core RAG components in LangchainKnowledge, including Document Loader, Text Splitter, Embeddings, and Retriever, to meet customization requirements in different scenarios.

## Custom Document Loader

1. Implement a custom Document Loader class

Since the LangchainKnowledge class invokes the `aload` method of the `BaseLoader` class to load documents, when customizing a Document Loader, you need to inherit from `BaseLoader` or its subclass and override the `aload` method (since the default implementation of `aload` in the `BaseLoader` class calls the `alazy_load` or `lazy_load` method, you only need to implement the `lazy_load` or `alazy_load` interface).

```python
from typing import AsyncIterator, Iterator

from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document

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
                    metadata={"line_number": line_number, "source": self.file_path},
                )
                line_number += 1

    # alazy_load is OPTIONAL.
    # If you leave out the implementation, a default implementation which delegates to lazy_load will be used!
    async def alazy_load(
        self,
    ) -> AsyncIterator[Document]:  # <-- Does not take any arguments
        """An async lazy loader that reads a file line by line."""
        # Requires aiofiles
        # https://github.com/Tinche/aiofiles
        import aiofiles

        async with aiofiles.open(self.file_path, encoding="utf-8") as f:
            line_number = 0
            async for line in f:
                yield Document(
                    page_content=line,
                    metadata={"line_number": line_number, "source": self.file_path},
                )
                line_number += 1
```

2. Construct a `LangchainKnowledge` object with the custom Document Loader

```python
rag = LangchainKnowledge(
    ...,
    document_loader=CustomDocumentLoader(file_path),
    ...,
)
```

## Custom Text Splitter

1. Implement a custom Text Splitter class

Since the LangchainKnowledge class invokes the `atransform_documents` method of the `BaseDocumentTransformer` class to process documents, when customizing a Text Splitter, you need to inherit from `BaseDocumentTransformer` or its subclass and override the `atransform_documents` method (since the default implementation of `atransform_documents` in the `BaseDocumentTransformer` class calls `transform_documents`, you can just implement `transform_documents`).

The following is an example of splitting text by a separator. For the complete example, see [custom_document_loader](../../../examples/ecosystem/langchain_knowledge/custom_text_splitter.py):

```python
from typing import Any, Sequence

from langchain_core.documents import BaseDocumentTransformer, Document

class CustomTextSplitter(BaseDocumentTransformer):
    """Interface for splitting text into chunks."""

    def __init__(
        self,
        separator: str
    ) -> None:
        """Create a new TextSplitter."""
        self.separator = separator

    def transform_documents(
        self, documents: Sequence[Document], **kwargs: Any
    ) -> Sequence[Document]:
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
                        }
                    )
                    transformed_docs.append(new_doc)

        return transformed_docs

    async def atransform_documents(
        self, documents: Sequence[Document], **kwargs: Any
    ) -> Sequence[Document]:
        """Asynchronously transform a list of documents.

        Args:
            documents: A sequence of Documents to be transformed.

        Returns:
            A sequence of transformed Documents.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self.transform_documents, documents, **kwargs
        )
```

2. Construct a `LangchainKnowledge` object with the custom Text Splitter

```python
rag = LangchainKnowledge(
    ...,
    document_transformer=CustomTextSplitter("\n"),
    ...,
)
```

## Custom Embeddings

1. Implement a custom Embeddings class

The approach to customizing embeddings is the same as [LangChain | Custom Embeddings](https://python.langchain.com/docs/how_to/custom_embeddings/). The following methods must be implemented:

| Method/Property | Description | Required/Optional |
|---|---|---|
| embed_documents(texts) | Generates embeddings for a list of strings. | Required |
| embed_query(text) | Generates an embedding for a single text query. | Required |
| aembed_documents(texts) | Asynchronously generates embeddings for a list of strings. | Optional |
| aembed_query(text) | Asynchronously generates an embedding for a single text query. | Optional |

The following is an example that converts text into a fixed vector (for illustration purposes only):

```python
from typing import List

from langchain_core.embeddings import Embeddings


class ParrotLinkEmbeddings(Embeddings):
    """ParrotLink embedding model integration."""

    def __init__(self, model: str):
        self.model = model

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed search docs."""
        return [[0.5, 0.6, 0.7] for _ in texts]

    def embed_query(self, text: str) -> List[float]:
        """Embed query text."""
        return self.embed_documents([text])[0]

    # optional: add custom async implementations here
    # you can also delete these, and the base class will
    # use the default implementation, which calls the sync
    # version in an async executor:

    # async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
    #     """Asynchronous Embed search docs."""
    #     ...

    # async def aembed_query(self, text: str) -> List[float]:
    #     """Asynchronous Embed query text."""
    #     ...
```

2. Construct a `LangchainKnowledge` object with the custom Embeddings

```python
rag = LangchainKnowledge(
    ...,
    embedder=ParrotLinkEmbeddings("xxx"),
    ...,
)
```

## Custom Retriever

1. Implement a custom Retriever class

The LangchainKnowledge class invokes the `ainvoke` method of the `BaseRetriever` class to perform retrieval. When both retriever and vectorstore are used together, the `from_documents` method of the `BaseRetriever` class is called to create the retriever from the vectorstore's indexed results.

The approach to customizing a Retriever is the same as [LangChain | How to create a custom Retriever](https://python.langchain.com/docs/how_to/custom_retriever/). The following methods must be implemented:

| Method/Property | Description | Required/Optional |
|---|---|---|
| _get_relevant_documents | Get documents relevant to a query. | Required |
| _aget_relevant_documents | Implement to provide async native support. | Optional |

The following is an example of a retriever that "returns all documents whose text contains the text from the user query". For the complete example, see [custom_document_loader](../../../examples/ecosystem/langchain_knowledge/custom_retriever.py):

```python
from typing import List

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever


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

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> List[Document]:
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
```

Additionally, if this Retriever needs to be used together with a vectorstore, it is required to have a `from_documents` interface. An example is shown below:

```python
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
```

2. Construct a `LangchainKnowledge` object with the custom Retriever

```python
test_documents = [
    Document(
        page_content="Shenzhen: sunny",
        metadata={"source": "weather.txt"}
    ),
    Document(
        page_content="Shanghai: cloud",
        metadata={"source": "weather.txt"}
    )
]
embedder=ToyRetriever(test_documents, k = 3)
rag = LangchainKnowledge(
    ...,
    embedder=embedder,
    ...,
)
```

## Complete Example

For the complete example, see [knowledge_with_custom_components](../../../examples/knowledge_with_custom_components/).

## References

[How to create a custom Document Loader](https://python.langchain.com/docs/how_to/document_loader_custom/)
[how_to/#custom](https://python.langchain.com/docs/how_to/#custom)
[Custom Embeddings](https://python.langchain.com/docs/how_to/custom_embeddings/)
[How to create a custom Retriever](https://python.langchain.com/docs/how_to/custom_retriever/)
