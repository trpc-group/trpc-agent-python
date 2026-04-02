# DocumentLoaders

DocumentLoaders are responsible for reading raw data from various data sources (text files, PDFs, Markdown, etc.) and converting them into the standard LangChain Document format for subsequent text splitting, vectorization, retrieval, and other downstream processes.

Each DocumentLoader has its own specific parameters, but they can all be invoked through a unified `.load` method.

Below is an introduction to some commonly used components:

- [TextLoader](#textloader)
- [PyPDFLoader](#pypdfloader)
- [UnstructuredMarkdownLoader](#unstructuredmarkdownloader)

For more components, refer to [Langchain Document loaders](https://python.langchain.com/docs/integrations/document_loaders/).

## TextLoader

### Install Dependencies

TextLoader is included in the langchain-community package. If langchain-community is not installed, use the following command to install it:

```shell
pip install langchain-community
```

### Usage

1. Create a `TextLoader` object

```python
import tempfile
from langchain_community.document_loaders import TextLoader

# Write text content to a temporary file and load it
text_content = "Artificial Intelligence (AI) is a branch of computer science..."
tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8")
tmp_file.write(text_content)
tmp_file.flush()
tmp_file.close()

# Create a TextLoader instance, specifying the temporary file path and encoding
text_loader = TextLoader(tmp_file.name, encoding="utf-8")
```

2. Construct a `LangchainKnowledge` object using this text_loader object

```python
rag = LangchainKnowledge(
    ...,
    document_loader=text_loader,
    ...,
)
```

### Reference

- [langchain_community.document_loaders.text.TextLoader](https://python.langchain.com/api_reference/community/document_loaders/langchain_community.document_loaders.text.TextLoader.html)


## PyPDFLoader

### Install Dependencies

```shell
pip install -qU pypdf
```

### Usage

1. Create a `PyPDFLoader` object

```python
import os
from langchain_community.document_loaders import PyPDFLoader

# Get the PDF file path from environment variables
pdf_path = os.getenv("DOCUMENT_PDF_PATH", "/path/to/your/file.pdf")
loader = PyPDFLoader(pdf_path)
```

2. Construct a `LangchainKnowledge` object using this loader object

```python
rag = LangchainKnowledge(
    ...,
    document_loader=loader,
    ...,
)
```

### Reference

- [How to load PDFs](https://python.langchain.com/docs/how_to/document_loader_pdf/)


## UnstructuredMarkdownLoader

### Install Dependencies

```shell
pip install -qU langchain_community unstructured
```

### Usage

1. Create an `UnstructuredMarkdownLoader` object

```python
import tempfile
from langchain_community.document_loaders import UnstructuredMarkdownLoader

# Write Markdown content to a temporary file and load it
md_content = "# Introduction to Artificial Intelligence\n\nArtificial Intelligence is a branch of computer science..."
tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".md", mode="w", encoding="utf-8")
tmp_file.write(md_content)
tmp_file.flush()
tmp_file.close()

# mode="single" treats the entire file as a single Document; strategy="fast" uses the fast parsing strategy
loader = UnstructuredMarkdownLoader(tmp_file.name, mode="single", strategy="fast")
```

2. Construct a `LangchainKnowledge` object using this loader object

```python
rag = LangchainKnowledge(
    ...,
    document_loader=loader,
    ...,
)
```

### Reference

- [UnstructuredMarkdownLoader](https://python.langchain.com/docs/integrations/document_loaders/unstructured_markdown/)

## Full Example

For a complete example, see [/examples/knowledge_with_documentloader/run_agent.py](../../../examples/knowledge_with_documentloader/run_agent.py).
