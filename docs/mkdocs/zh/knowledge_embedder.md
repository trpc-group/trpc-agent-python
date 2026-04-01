# Embedder 向量嵌入模型

Embeddings（向量嵌入模型）负责将文本、图像等非结构化数据映射为高维向量表示，使其语义可被计算和比较，是知识检索系统中实现语义搜索的核心组件。

以下是一些常用组件的用法介绍：

- [HuggingFaceEmbeddings](#huggingfaceembeddings)
- [HunyuanEmbeddings](#hunyuanembeddings)

更多组件使用说明详见 [Langchain Embedding models](https://python.langchain.com/docs/integrations/text_embedding/)。

## HuggingFaceEmbeddings

### 安装依赖

```shell
pip install --upgrade --quiet langchain langchain-huggingface sentence_transformers
```

### 使用

1. 创建 `HuggingFaceEmbeddings` 对象

```python
from langchain_huggingface import HuggingFaceEmbeddings

# 指定要使用的HuggingFace模型名称
model_name = "BAAI/bge-small-en-v1.5"
# 指定模型的加载参数，这里设置为在CPU上运行
model_kwargs = {"device": "cpu"}
# 指定编码参数，这里设置为对输出的向量进行归一化
encode_kwargs = {"normalize_embeddings": True}
# 创建 HuggingFaceEmbeddings 向量化器对象
embedder = HuggingFaceEmbeddings(
    model_name=model_name,
    model_kwargs=model_kwargs,
    encode_kwargs=encode_kwargs
)
```

2. 基于此 embedder 对象构造 `LangchainKnowledge` 对象

```python
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge

rag = LangchainKnowledge(
    prompt_template=rag_prompt,
    document_loader=text_loader,
    document_transformer=text_splitter,
    embedder=embedder, # 传入构建好的 Embedder
    vectorstore=vectorstore,
)
```

### 参考文档

- [Hugging Face](https://python.langchain.com/docs/integrations/providers/huggingface/)


## HunyuanEmbeddings

### 安装依赖

```shell
pip install hunyuan langchain-community
pip install "tencentcloud-sdk-python>=3.0.1139"
```

### 使用

1. 创建 `HunyuanEmbeddings` 对象

```python
from langchain_community.embeddings import HunyuanEmbeddings

embedder = HunyuanEmbeddings(
    hunyuan_secret_id="xxx",   # Hunyuan Secret ID, 或通过 HUNYUAN_SECRET_ID 环境变量设置
    hunyuan_secret_key="xxx",  # Hunyuan Secret Key，或通过 HUNYUAN_SECRET_KEY 环境变量设置
    region="ap-guangzhou"      # The region of hunyuan service
)
```

2. 基于此 embedder 对象构造 `LangchainKnowledge` 对象

```python
rag = LangchainKnowledge(
    ...,
    embedder=embedder,
    ...,
)
```

### 参考文档

- [langchain_community.embeddings.hunyuan.HunyuanEmbeddings](https://python.langchain.com/api_reference/community/embeddings/langchain_community.embeddings.hunyuan.HunyuanEmbeddings.html)
- [腾讯混元大模型 > 快速入门](https://cloud.tencent.com/document/product/1729/97730)
