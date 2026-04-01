# 自定义 Langchain RAG 组件

## 环境要求
Python版本: 3.10+（强烈建议使用3.12）

## 在trpc-agent-python框架代码下如何运行此代码示例

1. 下载trpc-agent-python代码并安装

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

2. 运行此代码示例

```bash
cd examples/knowledge_with_custom_components/
python3 run_agent.py
```

## 示例说明

本示例展示了如何自定义 LangchainKnowledge 的各个组件，包含以下三个演示：

- **Custom Document Loader**: 自定义文档加载器，继承`BaseLoader`，按行加载文件内容
- **Custom Text Splitter**: 自定义文本分割器，继承`BaseDocumentTransformer`，按分隔符分割文本
- **Custom Retriever**: 自定义检索器，继承`BaseRetriever`，返回包含查询文本的文档

### 自定义 Document Loader

由于LangchainKnowledge类会调用`BaseLoader`类的`aload`方法来加载文档，故自定义Document Loader时，你需要继承`BaseLoader`或其子类并重写`aload`方法（由于`BaseLoader`类的`aload`默认实现方法会调用`alazy_load`或`lazy_load`方法，故只需要实现`lazy_load`或`alazy_load`接口即可）。

### 自定义 Text Splitter

由于LangchainKnowledge类会调用`BaseDocumentTransformer`类的`atransform_documents`方法来加工文档，故自定义Text Splitter时，你需要继承`BaseDocumentTransformer`或其子类并重写`atransform_documents`方法（由于`BaseDocumentTransformer`类的`atransform_documents`方法默认实现会调用`transform_documents`，故可以只实现`transform_documents`）。

### 自定义 Retriever

LangchainKnowledge类会调用`BaseRetriever`类的`ainvoke`方法来进行检索，且当 retriever 和 vectorstore 同时使用时，会调用`BaseRetriever`类的`from_documents`方法从 vectorstore 的索引结果创建。

自定义Retriever的方式同[LangChain | How to create a custom Retriever](https://python.langchain.com/docs/how_to/custom_retriever/)。

## 参考文档

- [How to create a custom Document Loader](https://python.langchain.com/docs/how_to/document_loader_custom/)
- [how_to/#custom](https://python.langchain.com/docs/how_to/#custom)
- [Custom Embeddings](https://python.langchain.com/docs/how_to/custom_embeddings/)
- [How to create a custom Retriever](https://python.langchain.com/docs/how_to/custom_retriever/)
