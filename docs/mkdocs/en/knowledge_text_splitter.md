# Text Splitters

Text Splitters are responsible for splitting large documents into smaller text chunks suitable for retrieval. A well-designed text splitting strategy can effectively improve the retrieval accuracy and generation quality of a RAG system — by controlling chunk size and preserving semantic integrity, ensuring each text chunk contains meaningful contextual information.

Below is an introduction to some commonly used components:

- [MarkdownHeaderTextSplitter](#markdownheadertextsplitter)
- [RecursiveCharacterTextSplitter](#recursivecharactertextsplitter)

For more component usage details, see [Text splitters](https://python.langchain.com/docs/how_to/#text-splitters).

## Install Dependencies

MarkdownHeaderTextSplitter resides in different packages depending on the LangChain version:
- **LangChain 1.x.x**: `langchain-text-splitters` package
- **LangChain 0.3.x**: `langchain.text_splitter` module

After installing tRPC-Agent-Python, the relevant dependencies are installed automatically, so no further installation is required.

## MarkdownHeaderTextSplitter

### Usage

1. Create a `MarkdownHeaderTextSplitter` object

```python
# Import compatible with both LangChain 0.3.x and 1.x.x
try:
    from langchain_text_splitters import MarkdownHeaderTextSplitter
except ImportError:
    from langchain.text_splitter import MarkdownHeaderTextSplitter

headers_to_split_on = [
    ("#", "Header 1"),
    ("##", "Header 2"),
    ("###", "Header 3"),
]

markdown_splitter = MarkdownHeaderTextSplitter(headers_to_split_on)
```

2. Construct a `LangchainKnowledge` object using this `markdown_splitter` object

```python
rag = LangchainKnowledge(
    ...,
    document_transformer=markdown_splitter,
    ...,
)
```

### References

- [How to split Markdown by Headers](https://python.langchain.com/docs/how_to/markdown_header_metadata_splitter/)


## RecursiveCharacterTextSplitter

### Usage

1. Create a `RecursiveCharacterTextSplitter` object

```python
# Import compatible with both LangChain 0.3.x and 1.x.x
try:
    # Import for langchain v1.x.x
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    # Import for langchain v0.3.x
    from langchain.text_splitter import RecursiveCharacterTextSplitter

# chunk_size specifies the maximum number of characters per text chunk, chunk_overlap specifies the number of overlapping characters between adjacent chunks.
# Adjust these two parameters based on actual text length and use case to achieve optimal chunking results.
text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)
```

2. Construct a `LangchainKnowledge` object using this `text_splitter` object

```python
# examples/knowledge_with_rag_agent/agent/tools.py
rag = LangchainKnowledge(
    prompt_template=rag_prompt,
    document_loader=text_loader,
    document_transformer=text_splitter,
    embedder=embedder,
    vectorstore=vectorstore,
)
```

### References

- [How to recursively split text by characters](https://python.langchain.com/docs/how_to/recursive_text_splitter/)

## Complete Example

Please refer to [examples/knowledge_with_rag_agent/README.md](../../../examples/knowledge_with_rag_agent/README.md).
