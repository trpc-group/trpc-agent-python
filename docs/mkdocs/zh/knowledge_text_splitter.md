# Text Splitters 文本分割

Text Splitters（文本分割器）负责将大型文档拆分为适合检索的较小文本块。合理的文本分割策略能够有效提升 RAG 系统的检索精度与生成质量——通过控制分块大小、保留语义完整性，确保每个文本块都包含有意义的上下文信息。

以下是一些常用组件的用法介绍：

- [MarkdownHeaderTextSplitter](#markdownheadertextsplitter)
- [RecursiveCharacterTextSplitter](#recursivecharactertextsplitter)

更多组件使用说明详见 [Text splitters](https://python.langchain.com/docs/how_to/#text-splitters)。

## 安装依赖

MarkdownHeaderTextSplitter 在不同版本的 LangChain 中位于不同的包：
- **LangChain 1.x.x**: `langchain-text-splitters` 包
- **LangChain 0.3.x**: `langchain.text_splitter` 模块

在安装了 trpc-python-agent 框架后，相关依赖会自动安装，因此无需进一步安装依赖。

## MarkdownHeaderTextSplitter

### 使用

1. 创建 `MarkdownHeaderTextSplitter` 对象

```python
# 兼容 LangChain 0.3.x 和 1.x.x 的导入方式
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

2. 基于此 markdown_splitter 对象构造 `LangchainKnowledge` 对象

```python
rag = LangchainKnowledge(
    ...,
    document_transformer=markdown_splitter,
    ...,
)
```

### 参考文档

- [How to split Markdown by Headers](https://python.langchain.com/docs/how_to/markdown_header_metadata_splitter/)


## RecursiveCharacterTextSplitter

### 使用

1. 创建 `RecursiveCharacterTextSplitter` 对象

```python
# 兼容 LangChain 0.3.x 和 1.x.x 的导入方式
try:
    # langchain v1.x.x版本导入方式
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    # langchain v0.3.x版本导入方式
    from langchain.text_splitter import RecursiveCharacterTextSplitter

# 这里的 chunk_size 表示每个文本块的最大字符数，chunk_overlap 表示相邻文本块之间的重叠字符数。
# 可以根据实际文本的长度和应用场景调整这两个参数，以获得合适的分段效果。
text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)
```

2. 基于此 text_splitter 对象构造 `LangchainKnowledge` 对象

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

### 参考文档

- [How to recursively split text by characters](https://python.langchain.com/docs/how_to/recursive_text_splitter/)

## 完整示例

请参考 [examples/knowledge_with_rag_agent/README.md](../../../examples/knowledge_with_rag_agent/README.md)。
