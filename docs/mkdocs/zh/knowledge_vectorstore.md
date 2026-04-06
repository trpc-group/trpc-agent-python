# VectorStore（向量数据库）

tRPC-Python-Agent 框架通过 `LangchainKnowledge` 支持多种向量数据库后端。向量数据库（VectorStore）用于存储文本的向量化表示（Embedding），并基于向量相似度进行高效检索，是构建 RAG（检索增强生成）应用的核心组件。本文档介绍如何在框架中接入和使用以下向量数据库：

- [PGVector](#pgvector)
- [Elasticsearch](#elasticsearch)
- [Tencent Cloud VectorDB（腾讯云向量数据库）](#tencent-cloud-vectordb腾讯云向量数据库)

## PGVector

### 安装依赖

```bash
pip install -qU langchain-postgres
```

### 使用

创建 `PGVector` 对象并构造 `LangchainKnowledge`：

```python
def _build_pgvector_knowledge() -> LangchainKnowledge:
    """Build knowledge with PGVector vectorstore"""
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_postgres import PGVector

    config = get_pgvector_config()
    # 初始化 Embedding 模型，将文本转化为向量表示
    embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    # 创建 PGVector 实例；use_jsonb=True 使用 JSONB 格式存储元数据，支持结构化过滤
    vectorstore = PGVector(
        embeddings=embedder,
        collection_name=config["collection_name"],
        connection=config["connection"],
        use_jsonb=True,
    )
    text_loader = TextLoader(KNOWLEDGE_FILE, encoding="utf-8")
    # chunk_size 和 chunk_overlap 控制文档分块粒度，影响检索精度
    text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)

    # 将各组件组装为 LangchainKnowledge，统一管理文档加载、分割、向量化和检索
    return LangchainKnowledge(
        prompt_template=rag_prompt,
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=embedder,
        vectorstore=vectorstore,
    )
```

说明：确保启动了具有 pgvector 扩展的 PostgreSQL 数据库。连接参数通过环境变量配置，详见 [`examples/knowledge_with_vectorstore/agent/config.py`](../../../examples/knowledge_with_vectorstore/agent/config.py) 中的 `get_pgvector_config()`。

### 如何从文档构建向量数据库

如果是知识库已在PGVector上构建好，则可跳过此部分，直接进行检索即可。否则，可以按如下步骤进行构建。我们支持如下方式：

1) 使用`LangchainKnowledge`成员方法`create_vectorstore_from_document`构建向量数据库

```python
# examples/knowledge_with_vectorstore/run_agent.py
from agent.tools import rag

# 从文档创建向量数据库（如知识库已构建好，可跳过此步骤）
await rag.create_vectorstore_from_document()
```

2) 使用`PGVector`成员方法`add_documents`添加数据到向量数据库

以下为简单示例：

```python
from langchain_core.documents import Document

docs = [
    Document(
        page_content="there are cats in the pond",
        metadata={"id": 1, "location": "pond", "topic": "animals"},
    ),
    Document(
        page_content="ducks are also found in the pond",
        metadata={"id": 2, "location": "pond", "topic": "animals"},
    ),
    Document(
        page_content="fresh apples are available at the market",
        metadata={"id": 3, "location": "market", "topic": "food"},
    ),
]

# 将文档添加到向量数据库，ids 用于去重和后续更新
vectorstore.add_documents(docs, ids=[doc.metadata["id"] for doc in docs])
```

3) 直接使用`PGVector`的类方法`from_documents`构建向量数据库

```python
from langchain_community.document_loaders import TextLoader
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import CharacterTextSplitter
from langchain_postgres import PGVector
from agent.config import get_pgvector_config

config = get_pgvector_config()

# 加载并分割文档
loader = TextLoader("test.txt", encoding="utf-8")
documents = loader.load()
text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
docs = text_splitter.split_documents(documents)

embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")

# from_documents 一步完成向量化 + 写入数据库，返回可查询的 vectorstore 实例
vectorstore = PGVector.from_documents(
    documents=docs,
    embedding=embedder,
    collection_name=config["collection_name"],
    connection=config["connection"],
    use_jsonb=True,
)
```

### 如何使用向量数据库进行检索

1) 使用`LangchainKnowledge`的成员方法`search`

```python
# examples/knowledge_with_vectorstore/agent/tools.py
async def simple_search(query: str):
    """Search the knowledge base for relevant documents"""
    metadata = {
        'assistant_name': 'test',
        'runnable_config': {},
    }
    # 构造 Agent 上下文，timeout 设置检索超时（毫秒）
    ctx = new_agent_context(timeout=3000, metadata=metadata)
    # 将查询文本封装为 SearchRequest
    sr: SearchRequest = SearchRequest()
    sr.query = Part.from_text(text=query)
    # 执行向量相似度检索，返回按相关性排序的文档列表
    search_result: SearchResult = await rag.search(ctx, sr)
    if len(search_result.documents) == 0:
        return {"status": "failed", "report": "No documents found"}

    # 取相似度最高的首条文档
    best_doc = search_result.documents[0].document
    return {"status": "success", "report": f"content: {best_doc.page_content}"}
```

2) 使用`PGVector`的成员方法`similarity_search`

```python
results = vectorstore.similarity_search(
    query="LangChain provides abstractions to make working with LLMs easy",
    k=2,                                                          # 返回 Top-K 条最相似结果
    filter=[{"term": {"metadata.source.keyword": "tweet"}}],      # 基于元数据进行过滤
)
for res in results:
    print(f"* {res.page_content} [{res.metadata}]")
```

### 如何使用向量数据库创建检索器

使用`PGVector`的成员方法`as_retriever`获取检索器

```python
# search_type="mmr" 使用最大边际相关性算法，兼顾相关性与多样性
retriever = vectorstore.as_retriever(search_type="mmr", search_kwargs={"k": 1})
retriever.invoke("kitty")
```

### 参考文档

- [LangChain PGVector](https://python.langchain.com/docs/integrations/vectorstores/pgvector/)

---

## Elasticsearch

### 安装依赖

```bash
pip install -qU langchain-elasticsearch
```

### 使用

创建 `ElasticsearchStore` 对象并构造 `LangchainKnowledge`（完整代码见 `examples/knowledge_with_vectorstore/agent/tools.py`）：

```python
def _build_elasticsearch_knowledge() -> LangchainKnowledge:
    """Build knowledge with Elasticsearch vectorstore"""
    from langchain_elasticsearch import ElasticsearchStore
    from langchain_huggingface import HuggingFaceEmbeddings

    config = get_elasticsearch_config()
    embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    # 创建 ElasticsearchStore 实例，通过 es_api_key 进行身份认证
    vectorstore = ElasticsearchStore(
        es_url=config["es_url"],
        index_name=config["index_name"],
        embedding=embedder,
        es_api_key=config["es_api_key"],
    )
    text_loader = TextLoader(KNOWLEDGE_FILE, encoding="utf-8")
    text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)

    # 组装 LangchainKnowledge，与 PGVector 用法一致，仅 vectorstore 后端不同
    return LangchainKnowledge(
        prompt_template=rag_prompt,
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=embedder,
        vectorstore=vectorstore,
    )
```

说明：连接参数通过环境变量配置，详见 [`examples/knowledge_with_vectorstore/agent/config.py`](../../../examples/knowledge_with_vectorstore/agent/config.py) 中的 `get_elasticsearch_config()`。

### 如何从文档构建向量数据库

如果是知识库已在Elasticsearch上构建好，则可跳过此部分，直接进行检索即可。否则，可以按如下步骤进行构建。我们支持如下方式：

1) 使用`LangchainKnowledge`成员方法`create_vectorstore_from_document`构建向量数据库

```python
# examples/knowledge_with_vectorstore/run_agent.py
from agent.tools import rag, get_create_vectorstore_kwargs

# Elasticsearch 需要传递额外连接参数，由 get_create_vectorstore_kwargs() 自动生成
await rag.create_vectorstore_from_document(**get_create_vectorstore_kwargs())
```

`get_create_vectorstore_kwargs()` 对于 Elasticsearch 返回如下参数：
```python
def get_create_vectorstore_kwargs() -> dict:
    """Return extra kwargs for create_vectorstore_from_document based on vectorstore type"""
    vstore_type = get_vectorstore_type()
    # ...
    elif vstore_type == "elasticsearch":
        config = get_elasticsearch_config()
        # 这些参数会传递给 LangchainKnowledge.create_vectorstore_from_document()
        return {
            "es_url": config["es_url"],
            "index_name": config["index_name"],
            "es_api_key": config["es_api_key"],
        }
```

2) 使用`ElasticsearchStore`成员方法`add_documents`构建向量数据库

```python
import uuid
from agent.tools import rag

async def create_vectorstore_from_document():
    # 异步加载原始文档
    documents = await rag.document_loader.aload()
    # 将文档按配置的分割策略切分为小块
    documents = await rag.document_transformer.atransform_documents(documents)
    # 为每个文档块生成唯一 ID，确保可去重和更新
    uuids = [str(uuid.uuid4()) for _ in range(len(documents))]
    # 异步将文档块向量化并写入向量数据库
    added_ids = await rag.vectorstore.aadd_documents(documents=documents, ids=uuids)
    return added_ids
```

3) 直接使用`ElasticsearchStore`的类方法`from_documents`构建向量数据库

```python
from langchain_community.document_loaders import TextLoader
from langchain_elasticsearch import ElasticsearchStore
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import CharacterTextSplitter
from agent.config import get_elasticsearch_config

config = get_elasticsearch_config()

# 加载并分割文档
loader = TextLoader("test.txt", encoding="utf-8")
documents = loader.load()
text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
docs = text_splitter.split_documents(documents)

embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")

# from_documents 一步完成向量化 + 写入 Elasticsearch，返回可查询的 vectorstore 实例
vectorstore = ElasticsearchStore.from_documents(
    documents=docs,
    embedding=embedder,
    es_url=config["es_url"],
    index_name=config["index_name"],
    es_api_key=config["es_api_key"],
)
```

### 如何使用向量数据库进行检索

1) 使用`LangchainKnowledge`的成员方法`search`

```python
# examples/knowledge_with_vectorstore/agent/tools.py
async def simple_search(query: str):
    """Search the knowledge base for relevant documents"""
    metadata = {
        'assistant_name': 'test',
        'runnable_config': {},
    }
    # 构造 Agent 上下文，timeout 设置检索超时（毫秒）
    ctx = new_agent_context(timeout=3000, metadata=metadata)
    # 将查询文本封装为 SearchRequest
    sr: SearchRequest = SearchRequest()
    sr.query = Part.from_text(text=query)
    # 执行向量相似度检索，返回按相关性排序的文档列表
    search_result: SearchResult = await rag.search(ctx, sr)
    if len(search_result.documents) == 0:
        return {"status": "failed", "report": "No documents found"}

    # 取相似度最高的首条文档
    best_doc = search_result.documents[0].document
    return {"status": "success", "report": f"content: {best_doc.page_content}"}
```

2) 使用`ElasticsearchStore`的成员方法`similarity_search`

```python
results = vectorstore.similarity_search(
    query="LangChain provides abstractions to make working with LLMs easy",
    k=2,                                                          # 返回 Top-K 条最相似结果
    filter=[{"term": {"metadata.source.keyword": "tweet"}}],      # 基于元数据进行过滤
)
for res in results:
    print(f"* {res.page_content} [{res.metadata}]")
```

3) 设置检索策略：使用`ElasticsearchStore`的类方法`from_documents`，通过参数`strategy`设置检索策略

```python
from langchain_elasticsearch import DenseVectorStrategy
from agent.config import get_elasticsearch_config

config = get_elasticsearch_config()

vectorstore = ElasticsearchStore.from_documents(
    documents=docs,
    embedding=embedder,
    es_url=config["es_url"],
    index_name=config["index_name"],
    es_api_key=config["es_api_key"],
    strategy=DenseVectorStrategy(),  # 使用稠密向量检索策略，也可选 SparseVectorStrategy 等
)

docs = vectorstore.similarity_search(
    query="What did the president say about Ketanji Brown Jackson?", k=10
)
```

说明：更多检索策略详见[LangChain Elasticsearch Retrieval Strategies](https://python.langchain.com/docs/integrations/vectorstores/elasticsearch/#retrieval-strategies)

### 如何使用向量数据库创建检索器

1) 使用`ElasticsearchStore`的成员方法`as_retriever`获取检索器

```python
# similarity_score_threshold 仅返回相似度高于阈值的结果，过滤低质量匹配
retriever = vectorstore.as_retriever(
    search_type="similarity_score_threshold", search_kwargs={"score_threshold": 0.2}
)
retriever.invoke("Stealing from the bank is a crime")
```

2) 使用`ElasticsearchRetriever`的类方法`from_es_params`创建检索器

```python
from langchain_elasticsearch import ElasticsearchRetriever

# 自定义向量查询函数，返回 Elasticsearch 原生 KNN 查询 DSL
def vector_query(search_query: str) -> Dict:
    vector = embeddings.embed_query(search_query)  # 使用与索引时相同的 Embedding 模型
    return {
        "knn": {
            "field": dense_vector_field,
            "query_vector": vector,
            "k": 5,              # 返回最相似的 5 条结果
            "num_candidates": 10, # 候选集大小，越大精度越高但性能越低
        }
    }

# 通过 ES 原生参数创建检索器，body_func 允许完全自定义查询逻辑
vector_retriever = ElasticsearchRetriever.from_es_params(
    index_name=index_name,
    body_func=vector_query,
    content_field=text_field,
    url=es_url,
)

vector_retriever.invoke("foo")
```

说明：更多类型的检索器详见[LangChain Elasticsearch Retriever](https://python.langchain.com/docs/integrations/retrievers/elasticsearch_retriever/)

### 参考文档

- [LangChain Elasticsearch](https://python.langchain.com/docs/integrations/vectorstores/elasticsearch/)
- [LangChain Elasticsearch Retriever](https://python.langchain.com/docs/integrations/retrievers/elasticsearch_retriever/)

---

## Tencent Cloud VectorDB（腾讯云向量数据库）

### 安装依赖

```bash
pip3 install tcvectordb langchain-community
```

### 使用

创建 `TencentVectorDB` 对象并构造 `LangchainKnowledge`：

```python
def _build_tencentvdb_knowledge() -> LangchainKnowledge:
    """Build knowledge with Tencent Cloud VectorDB"""
    from langchain_community.vectorstores.tencentvectordb import (
        ConnectionParams,
        IndexParams,
        TencentVectorDB,
    )

    config = get_tencentvdb_config()
    connection_params = ConnectionParams(
        url=config["url"],
        key=config["key"],
        username=config["username"],
        timeout=20,
    )
    # dimension 需与所用 Embedding 模型的输出维度一致
    index_params = IndexParams(dimension=768, replicas=0)
    # 腾讯云向量数据库支持服务端内置 Embedding，无需外部 Embedding 模型，因此传 None
    embeddings = None
    vectorstore = TencentVectorDB(
        embedding=embeddings,
        connection_params=connection_params,
        index_params=index_params,
        database_name=config["database_name"],
        collection_name=config["collection_name"],
        t_vdb_embedding=config["t_vdb_embedding"],  # 指定服务端内置的 Embedding 模型
    )
    text_loader = TextLoader(KNOWLEDGE_FILE, encoding="utf-8")
    text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)

    # embedder 传 None，因为向量化由腾讯云服务端完成
    return LangchainKnowledge(
        prompt_template=rag_prompt,
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=None,
        vectorstore=vectorstore,
    )
```

说明：连接参数通过环境变量配置，详见 [`examples/knowledge_with_vectorstore/agent/config.py`](../../../examples/knowledge_with_vectorstore/agent/config.py) 中的 `get_tencentvdb_config()`。

### 如何从文档构建向量数据库

如果是知识库已在腾讯云向量数据库上构建好，则可跳过此部分，直接进行检索即可。否则，可以按如下步骤进行构建。我们支持如下方式：

1) 使用`LangchainKnowledge`成员方法`create_vectorstore_from_document`构建向量数据库

```python
# examples/knowledge_with_vectorstore/run_agent.py
from agent.tools import rag, get_create_vectorstore_kwargs

# 腾讯云向量数据库需要传递额外连接参数，由 get_create_vectorstore_kwargs() 自动生成
await rag.create_vectorstore_from_document(**get_create_vectorstore_kwargs())
```

`get_create_vectorstore_kwargs()` 对于腾讯云向量数据库返回如下参数：

```python
def get_create_vectorstore_kwargs() -> dict:
    """Return extra kwargs for create_vectorstore_from_document based on vectorstore type"""
    vstore_type = get_vectorstore_type()
    if vstore_type == "tencentvdb":
        from langchain_community.vectorstores.tencentvectordb import (
            ConnectionParams,
            IndexParams,
        )
        config = get_tencentvdb_config()
        # 这些参数会传递给 LangchainKnowledge.create_vectorstore_from_document()
        # 用于在腾讯云服务端创建向量数据库集合
        return {
            "embeddings": None,
            "connection_params": ConnectionParams(
                url=config["url"],
                key=config["key"],
                username=config["username"],
                timeout=20,
            ),
            "index_params": IndexParams(dimension=768, replicas=0),
            "database_name": config["database_name"],
            "collection_name": config["collection_name"],
            "t_vdb_embedding": config["t_vdb_embedding"],
        }
```

2) 直接使用`TencentVectorDB`的类方法`from_documents`构建向量数据库

```python
from langchain_community.document_loaders import TextLoader
from langchain_community.vectorstores.tencentvectordb import (
    ConnectionParams,
    TencentVectorDB,
)
from langchain_text_splitters import CharacterTextSplitter
from agent.config import get_tencentvdb_config

config = get_tencentvdb_config()

loader = TextLoader("test.txt", encoding="utf-8")
documents = loader.load()
text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
docs = text_splitter.split_documents(documents)

conn_params = ConnectionParams(
    url=config["url"],
    key=config["key"],
    username=config["username"],
    timeout=20,
)

# 使用服务端内置 Embedding，无需本地 Embedding 模型
embeddings = None

vector_db = TencentVectorDB.from_documents(
    docs,
    embeddings=embeddings,
    connection_params=conn_params,
    t_vdb_embedding=config["t_vdb_embedding"],  # 指定服务端 Embedding 模型名称
)
```

说明：from_documents间接调用from_text接口，支持传递更多的接口参数（如数据库名、connection名等），详见from_texts接口定义：

```python
    def from_texts(
        cls,
        texts: List[str],
        embedding: Embeddings,
        metadatas: Optional[List[dict]] = None,
        connection_params: Optional[ConnectionParams] = None,
        index_params: Optional[IndexParams] = None,
        database_name: str = "LangChainDatabase",
        collection_name: str = "LangChainCollection",
        drop_old: Optional[bool] = False,
        collection_description: Optional[str] = "Collection for LangChain",
        meta_fields: Optional[List[MetaField]] = None,
        t_vdb_embedding: Optional[str] = "bge-base-zh",
        **kwargs: Any,
    ) -> TencentVectorDB:
```

3) 使用`TencentVectorDB`的类方法`add_texts`往向量数据库中插入数据

```python
# 若未指定文档id，则会随机生成。可通过 ids: Optional[List[str]]参数指定
vector_db.add_texts(["Ankush went to Princeton"])
```

### 如何使用向量数据库进行检索

1) 使用`LangchainKnowledge`成员方法`search`

```python
# examples/knowledge_with_vectorstore/agent/tools.py
async def simple_search(query: str):
    """Search the knowledge base for relevant documents"""
    metadata = {
        'assistant_name': 'test',
        'runnable_config': {},
    }
    # 构造 Agent 上下文，timeout 设置检索超时（毫秒）
    ctx = new_agent_context(timeout=3000, metadata=metadata)
    # 将查询文本封装为 SearchRequest
    sr: SearchRequest = SearchRequest()
    sr.query = Part.from_text(text=query)
    # 执行向量相似度检索，返回按相关性排序的文档列表
    search_result: SearchResult = await rag.search(ctx, sr)
    if len(search_result.documents) == 0:
        return {"status": "failed", "report": "No documents found"}

    # 取相似度最高的首条文档
    best_doc = search_result.documents[0].document
    return {"status": "success", "report": f"content: {best_doc.page_content}"}
```

2) 使用`TencentVectorDB`的成员方法`similarity_search`

```python
query = "What did the president say about Ketanji Brown Jackson"
docs = vector_db.similarity_search(query)
print(docs[0].page_content)
```

### 参考文档

- [LangChain Tencent Cloud VectorDB](https://python.langchain.com/docs/integrations/vectorstores/tencentvectordb/)
- [腾讯云向量数据库](https://cloud.tencent.com/document/product/1709)

## 完整示例

完整示例可见 [knowledge_with_vectorstore](../../../examples/knowledge_with_vectorstore/)，支持 PGVector、Elasticsearch、腾讯云向量数据库三种后端。

示例运行步骤：

1. 在 `examples/knowledge_with_vectorstore/.env` 中配置环境变量，设置 `VECTORSTORE_TYPE=tencentvdb` 并填写腾讯云向量数据库连接参数：

```bash
VECTORSTORE_TYPE=tencentvdb
TENCENT_VDB_URL=http://10.0.X.X
TENCENT_VDB_KEY=your_key_here
TENCENT_VDB_USERNAME=root
TENCENT_VDB_DATABASE=LangChainDatabase
TENCENT_VDB_COLLECTION=LangChainCollection
TENCENT_VDB_EMBEDDING=bge-base-zh
```

2. 准备知识库文档

默认使用项目下的 `test.txt` 作为知识库文档。你可以替换为自己的文档，或通过 `KNOWLEDGE_FILE` 环境变量指定路径：

```shell
echo "shenzhen weather: sunny
guangzhou weather: rain
shanghai weather: cloud" > test.txt
```

3. 运行示例

```shell
cd examples/knowledge_with_vectorstore/
python3 run_agent.py
```

程序会自动从文档构建向量数据库，然后 Agent 接收查询后调用 `simple_search` 工具进行向量检索，结合检索结果生成回答。
