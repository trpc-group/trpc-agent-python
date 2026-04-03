# Retrievers 检索器

Retrievers（检索器）是 LangChain 中用于根据查询返回相关文档的通用接口。与向量存储不同，检索器不需要具备文档存储能力，只需负责检索和返回文档。检索器既可以基于向量存储构建，也支持更多样化的检索后端，例如 [Wikipedia search](https://python.langchain.com/docs/integrations/retrievers/wikipedia/) 和 [Amazon Kendra](https://python.langchain.com/docs/integrations/retrievers/amazon_kendra_retriever/)。

根据使用的检索器类型，有如下几种方法可创建检索器：

- [向量数据库创建检索器](#向量数据库创建检索器)
- [BM25Retriever](#bm25retriever)

更多组件使用说明详见 [LangChain Retrievers](https://python.langchain.com/docs/integrations/retrievers/)。

## 向量数据库创建检索器

### 使用

1. 创建 `retriever` 对象

可以直接基于 vectorstore 实例化得到其相关的 retriever：

```python
retriever = vectorstore.as_retriever()  # 使用vectorstore的as_retriever方法获取

docs = retriever.invoke("your-question?")  # 进行检索
```

同时你可以指定 search type 及更多的 search parameters，详情参考 [How to use a vectorstore as a retriever](https://python.langchain.com/docs/how_to/vectorstore_retriever/)。

2. 基于此 retriever 对象构造 `LangchainKnowledge` 对象

```python
rag = LangchainKnowledge(
    ...,
    retriever=retriever,
    ...,
)
```

> **说明：** 若已使用了 `vectorstore`，则 `retriever` 不是必须的。若 `vectorstore` 与 `retriever` 同时使用，`retriever` 将对 `vectorstore` 的结果进行重排序，然后输出检索结果；这时要求 `retriever` 对象具有 `from_documents` 接口（用于从 vectorstore 结果中创建检索集）。

### 参考文档

- [How to use a vectorstore as a retriever](https://python.langchain.com/docs/how_to/vectorstore_retriever/)

## BM25Retriever

### 安装依赖

```shell
pip install --upgrade --quiet rank_bm25
```

### 使用

1. 创建 `BM25Retriever` 对象

```python
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document

# 使用 BM25Retriever 的 from_texts 方法构造检索器
# 给定一些示例文档内容 ["foo", "bar"]
retriever = BM25Retriever.from_texts(["foo", "bar"])

# 或使用 from_documents 创建
# retriever = BM25Retriever.from_documents(
#     [
#         Document(page_content="foo"),
#         Document(page_content="bar"),
#     ]
# )
```

2. 基于此 retriever 对象构造 `LangchainKnowledge` 对象

```python
rag = LangchainKnowledge(
    ...,
    retriever=retriever,
    ...,
)
```

### 参考文档

- [BM25Retriever](https://python.langchain.com/docs/integrations/retrievers/bm25/)

## 完整示例

请参考 [examples/knowledge_with_rag_agent/README.md](../../../examples/knowledge_with_rag_agent/README.md)。

## 更多内容
更多 Retriever 组件使用说明可以参考：[LangChain Retrievers](https://python.langchain.com/docs/integrations/retrievers/)。
