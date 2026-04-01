# DocumentLoaders 文档加载

DocumentLoaders（文档加载器）负责从多种数据源（文本文件、PDF、Markdown 等）读取原始数据，并将其转换为标准的 LangChain Document 格式，以便后续的文本分割、向量化和检索等流程使用。

每个 DocumentLoader 都有其特定的参数，但它们都可以通过统一的 `.load` 方法进行调用。

以下是一些常用组件的用法介绍：

- [TextLoader](#textloader)
- [PyPDFLoader](#pypdfloader)
- [UnstructuredMarkdownLoader](#unstructuredmarkdownloader)

更多组件使用说明详见 [Langchain Document loaders](https://python.langchain.com/docs/integrations/document_loaders/)。

## TextLoader

### 安装依赖

TextLoader 位于 langchain-community package 中，如未安装 langchain-community，可使用如下命令安装：

```shell
pip install langchain-community
```

### 使用

1. 创建 `TextLoader` 对象

```python
import tempfile
from langchain_community.document_loaders import TextLoader

# 将文本写入临时文件后加载
text_content = "人工智能（Artificial Intelligence，简称AI）是计算机科学的一个分支..."
tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8")
tmp_file.write(text_content)
tmp_file.flush()
tmp_file.close()

# 创建 TextLoader 实例，指定临时文件路径和编码格式
text_loader = TextLoader(tmp_file.name, encoding="utf-8")
```

2. 基于此 text_loader 对象构造 `LangchainKnowledge` 对象

```python
rag = LangchainKnowledge(
    ...,
    document_loader=text_loader,
    ...,
)
```

### 参考文档

- [langchain_community.document_loaders.text.TextLoader](https://python.langchain.com/api_reference/community/document_loaders/langchain_community.document_loaders.text.TextLoader.html)


## PyPDFLoader

### 安装依赖

```shell
pip install -qU pypdf
```

### 使用

1. 创建 `PyPDFLoader` 对象

```python
import os
from langchain_community.document_loaders import PyPDFLoader

# 从环境变量获取 PDF 文件路径
pdf_path = os.getenv("DOCUMENT_PDF_PATH", "/path/to/your/file.pdf")
loader = PyPDFLoader(pdf_path)
```

2. 基于此 loader 对象构造 `LangchainKnowledge` 对象

```python
rag = LangchainKnowledge(
    ...,
    document_loader=loader,
    ...,
)
```

### 参考文档

- [How to load PDFs](https://python.langchain.com/docs/how_to/document_loader_pdf/)


## UnstructuredMarkdownLoader

### 安装依赖

```shell
pip install -qU langchain_community unstructured
```

### 使用

1. 创建 `UnstructuredMarkdownLoader` 对象

```python
import tempfile
from langchain_community.document_loaders import UnstructuredMarkdownLoader

# 将 Markdown 内容写入临时文件后加载
md_content = "# 人工智能简介\n\n人工智能是计算机科学的一个分支..."
tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".md", mode="w", encoding="utf-8")
tmp_file.write(md_content)
tmp_file.flush()
tmp_file.close()

# mode="single" 将整个文件作为一个 Document，strategy="fast" 使用快速解析策略
loader = UnstructuredMarkdownLoader(tmp_file.name, mode="single", strategy="fast")
```

2. 基于此 loader 对象构造 `LangchainKnowledge` 对象

```python
rag = LangchainKnowledge(
    ...,
    document_loader=loader,
    ...,
)
```

### 参考文档

- [UnstructuredMarkdownLoader](https://python.langchain.com/docs/integrations/document_loaders/unstructured_markdown/)

## 完整示例

完整示例见 [/examples/knowledge_with_documentloader/run_agent.py](../../examples/knowledge_with_documentloader/run_agent.py)。
