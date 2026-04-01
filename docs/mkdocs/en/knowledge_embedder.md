# Embedder

Embeddings are responsible for mapping unstructured data such as text and images into high-dimensional vector representations, enabling their semantics to be computed and compared. They are a core component for implementing semantic search in knowledge retrieval systems.

Below is an introduction to some commonly used components:

- [HuggingFaceEmbeddings](#huggingfaceembeddings)
- [HunyuanEmbeddings](#hunyuanembeddings)

For more components, refer to [Langchain Embedding models](https://python.langchain.com/docs/integrations/text_embedding/).

## HuggingFaceEmbeddings

### Install Dependencies

```shell
pip install --upgrade --quiet langchain langchain-huggingface sentence_transformers
```

### Usage

1. Create a `HuggingFaceEmbeddings` object

```python
from langchain_huggingface import HuggingFaceEmbeddings

# Specify the HuggingFace model name to use
model_name = "BAAI/bge-small-en-v1.5"
# Specify model loading parameters; here it is set to run on CPU
model_kwargs = {"device": "cpu"}
# Specify encoding parameters; here it is set to normalize the output embeddings
encode_kwargs = {"normalize_embeddings": True}
# Create the HuggingFaceEmbeddings embedder object
embedder = HuggingFaceEmbeddings(
    model_name=model_name,
    model_kwargs=model_kwargs,
    encode_kwargs=encode_kwargs
)
```

2. Construct a `LangchainKnowledge` object using this embedder object

```python
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge

rag = LangchainKnowledge(
    prompt_template=rag_prompt,
    document_loader=text_loader,
    document_transformer=text_splitter,
    embedder=embedder, # Pass in the constructed Embedder
    vectorstore=vectorstore,
)
```

### Reference

- [Hugging Face](https://python.langchain.com/docs/integrations/providers/huggingface/)


## HunyuanEmbeddings

### Install Dependencies

```shell
pip install hunyuan langchain-community
pip install "tencentcloud-sdk-python>=3.0.1139"
```

### Usage

1. Create a `HunyuanEmbeddings` object

```python
from langchain_community.embeddings import HunyuanEmbeddings

embedder = HunyuanEmbeddings(
    hunyuan_secret_id="xxx",   # Hunyuan Secret ID, or set via the HUNYUAN_SECRET_ID environment variable
    hunyuan_secret_key="xxx",  # Hunyuan Secret Key, or set via the HUNYUAN_SECRET_KEY environment variable
    region="ap-guangzhou"      # The region of hunyuan service
)
```

2. Construct a `LangchainKnowledge` object using this embedder object

```python
rag = LangchainKnowledge(
    ...,
    embedder=embedder,
    ...,
)
```

### Reference

- [langchain_community.embeddings.hunyuan.HunyuanEmbeddings](https://python.langchain.com/api_reference/community/embeddings/langchain_community.embeddings.hunyuan.HunyuanEmbeddings.html)
- [Tencent Hunyuan LLM > Quick Start](https://cloud.tencent.com/document/product/1729/97730)
