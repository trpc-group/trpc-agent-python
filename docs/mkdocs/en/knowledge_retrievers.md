# Retrievers

Retrievers are a generic interface in LangChain for returning relevant documents based on a query. Unlike vector stores, retrievers do not need to have document storage capabilities — they are only responsible for retrieving and returning documents. Retrievers can be built on top of vector stores, but also support more diverse retrieval backends, such as [Wikipedia search](https://python.langchain.com/docs/integrations/retrievers/wikipedia/) and [Amazon Kendra](https://python.langchain.com/docs/integrations/retrievers/amazon_kendra_retriever/).

Depending on the type of retriever used, there are several ways to create a retriever:

- [Create Retriever from Vector Store](#create-retriever-from-vector-store)
- [BM25Retriever](#bm25retriever)

For more component usage details, see [LangChain Retrievers](https://python.langchain.com/docs/integrations/retrievers/).

## Create Retriever from Vector Store

### Usage

1. Create a `retriever` object

You can directly instantiate a retriever from a vectorstore instance:

```python
retriever = vectorstore.as_retriever()  # Use the vectorstore's as_retriever method

docs = retriever.invoke("your-question?")  # Perform retrieval
```

You can also specify the search type and additional search parameters. For details, refer to [How to use a vectorstore as a retriever](https://python.langchain.com/docs/how_to/vectorstore_retriever/).

2. Construct a `LangchainKnowledge` object based on this retriever object

```python
rag = LangchainKnowledge(
    ...,
    retriever=retriever,
    ...,
)
```

> **Note:** If a `vectorstore` is already in use, the `retriever` is not required. If both `vectorstore` and `retriever` are used simultaneously, the `retriever` will re-rank the results from the `vectorstore` before outputting the retrieval results. In this case, the `retriever` object must have a `from_documents` interface (used to create a retrieval set from vectorstore results).

### Reference

- [How to use a vectorstore as a retriever](https://python.langchain.com/docs/how_to/vectorstore_retriever/)

## BM25Retriever

### Installation

```shell
pip install --upgrade --quiet rank_bm25
```

### Usage

1. Create a `BM25Retriever` object

```python
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document

# Create a retriever using BM25Retriever's from_texts method
# Given some example document contents ["foo", "bar"]
retriever = BM25Retriever.from_texts(["foo", "bar"])

# Or create using from_documents
# retriever = BM25Retriever.from_documents(
#     [
#         Document(page_content="foo"),
#         Document(page_content="bar"),
#     ]
# )
```

2. Construct a `LangchainKnowledge` object based on this retriever object

```python
rag = LangchainKnowledge(
    ...,
    retriever=retriever,
    ...,
)
```

### Reference

- [BM25Retriever](https://python.langchain.com/docs/integrations/retrievers/bm25/)

## Complete Example

Please refer to [`examples/knowledge_with_rag_agent`](../../../examples/knowledge_with_rag_agent).

## More

For more Retriever component usage details, refer to: [LangChain Retrievers](https://python.langchain.com/docs/integrations/retrievers/).
