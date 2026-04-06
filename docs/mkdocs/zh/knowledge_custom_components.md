# 自定义 Langchain RAG 组件

本文介绍如何自定义 `LangchainKnowledge` 中的 RAG 核心组件，包括 `Document Loader`、`Text Splitter`、`Embeddings` 和 `Retriever`，以满足不同场景下的定制化需求。

## 自定义 Document Loader

1. 实现自定义 Document Loader 类

由于 `LangchainKnowledge` 会调用 `BaseLoader` 的 `aload` 方法来加载文档，因此在自定义 `Document Loader` 时，你需要继承 `BaseLoader` 或其子类并重写 `aload`（`BaseLoader.aload` 的默认实现会调用 `alazy_load` 或 `lazy_load`，因此只实现 `lazy_load` 或 `alazy_load` 也可以）。

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

2. 基于自定义的 `Document Loader` 构造 `LangchainKnowledge` 对象

```python
rag = LangchainKnowledge(
    ...,
    document_loader=CustomDocumentLoader(file_path),
    ...,
)
```

## 自定义 Text Splitter

1. 实现自定义 Text Splitter 类

由于 `LangchainKnowledge` 会调用 `BaseDocumentTransformer` 的 `atransform_documents` 方法来加工文档，因此在自定义 `Text Splitter` 时，你需要继承 `BaseDocumentTransformer` 或其子类并重写 `atransform_documents`（`BaseDocumentTransformer.atransform_documents` 的默认实现会调用 `transform_documents`，因此只实现 `transform_documents` 也可以）。

一个按 `separator` 分隔符切分文本的示例如下，完整示例见 [knowledge_with_custom_components](../../../examples/knowledge_with_custom_components/)：

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

2. 基于自定义的 `Text Splitter` 构造 `LangchainKnowledge` 对象

```python
rag = LangchainKnowledge(
    ...,
    document_transformer=CustomTextSplitter("\n"),
    ...,
)
```

## 自定义 Embeddings

1. 实现自定义 Embeddings 类

自定义 Embeddings 的方式与 [LangChain | Custom Embeddings](https://python.langchain.com/docs/how_to/custom_embeddings/) 一致，必须实现以下方法：

| Method/Property | Description | Required/Optional |
|---|---|---|
| embed_documents(texts) | Generates embeddings for a list of strings. | Required |
| embed_query(text) | Generates an embedding for a single text query. | Required |
| aembed_documents(texts) | Asynchronously generates embeddings for a list of strings. | Optional |
| aembed_query(text) | Asynchronously generates an embedding for a single text query. | Optional |

一个“将文本转为固定向量”的示例（仅用于说明意图）如下：

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

2. 基于自定义的 `Embeddings` 构造 `LangchainKnowledge` 对象

```python
rag = LangchainKnowledge(
    ...,
    embedder=ParrotLinkEmbeddings("xxx"),
    ...,
)
```

## 自定义 Retriever

1. 实现自定义 Retriever 类

`LangchainKnowledge` 会调用 `BaseRetriever` 的 `ainvoke` 方法进行检索；当 retriever 与 vectorstore 同时使用时，会调用 `BaseRetriever` 的 `from_documents` 方法，从 vectorstore 的索引结果构建 retriever。

自定义 `Retriever` 的方式与 [LangChain | How to create a custom Retriever](https://python.langchain.com/docs/how_to/custom_retriever/) 一致，必须实现以下方法：

| Method/Property | Description | Required/Optional |
|---|---|---|
| _get_relevant_documents | Get documents relevant to a query. | Required |
| _aget_relevant_documents | Implement to provide async native support. | Optional |

一个“返回文本包含用户查询中的文本的所有文档”的检索器示例如下，完整示例见[knowledge_with_custom_components](../../../examples/knowledge_with_custom_components/)：

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

另外，若该 Retriever 需要与 vectorstore 一起使用，则要求实现 `from_documents` 接口，示例如下：

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

2. 基于自定义的 `Retriever` 构造 `LangchainKnowledge` 对象

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
retriever = ToyRetriever(test_documents, k=3)
rag = LangchainKnowledge(
    ...,
    retriever=retriever,
    ...,
)
```

## 完整示例

完整示例见 [knowledge_with_custom_components](../../../examples/knowledge_with_custom_components/)。

## 参考文档

- [How to create a custom Document Loader](https://python.langchain.com/docs/how_to/document_loader_custom/)
- [how_to/#custom](https://python.langchain.com/docs/how_to/#custom)
- [Custom Embeddings](https://python.langchain.com/docs/how_to/custom_embeddings/)
- [How to create a custom Retriever](https://python.langchain.com/docs/how_to/custom_retriever/)
