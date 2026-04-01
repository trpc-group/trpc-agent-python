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

1) 创建`PGVector`对象

```python
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_postgres import PGVector

connection = "postgresql+psycopg://langchain:langchain@X.X.X.X:5432/langchain"  # Uses psycopg3!
collection_name = "my_docs"
embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")

vectorstore = PGVector(
    embeddings=embedder,
    collection_name=collection_name,
    connection=connection,
    use_jsonb=True,
)
```

说明：确保启动了具有 pgvector 扩展的 PostgreSQL 数据库

2) 基于此vectorstore对象构造`LangchainKnowledge`对象

```python
rag = LangchainKnowledge(
    ...,
    vectorstore=vectorstore,
    ...,
)
```

### 如何从文档构建向量数据库

如果是知识库已在PGVector上构建好，则可跳过此部分，直接进行检索即可。否则，可以按如下步骤进行构建。我们支持如下方式：

1) 使用`LangchainKnowledge`成员方法`create_vectorstore_from_document`构建向量数据库

```python
import asyncio
import uuid

# Compatible imports for LangChain 0.3.x and 1.x.x
try:
    # langchain v1.x.x版本导入方式
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    # langchain v0.3.x版本导入方式
    from langchain.text_splitter import RecursiveCharacterTextSplitter

from langchain_community.document_loaders import TextLoader
from langchain_core.prompts import ChatPromptTemplate
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_postgres import PGVector
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge

connection = "postgresql+psycopg://langchain:langchain@X.X.X.X:X/langchain"  # Uses psycopg3!
collection_name = "my_docs"

def build_chain():
    template = """Answer the question gently:
    Query: {query}
    """
    prompt = ChatPromptTemplate.from_template(template)
    embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    vectorstore = PGVector(
        embeddings=embedder,
        collection_name=collection_name,
        connection=connection,
        use_jsonb=True,
    )
    text_loader = TextLoader("/trpc-agent/examples/agents/pgvector_rag_test.txt", encoding="utf-8")
    text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)

    rag = LangchainKnowledge(
        prompt_template=prompt,
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=embedder,
        vectorstore=vectorstore,
    )
    return rag

rag = build_chain()

async def create_vectorstore_from_document():
    await rag.create_vectorstore_from_document()

asyncio.run(create_vectorstore_from_document())
```

2) 使用`PGVector`成员方法`add_documents`添加数据到向量数据库

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

vectorstore.add_documents(docs, ids=[doc.metadata["id"] for doc in docs])
```

3) 直接使用`PGVector`的类方法`from_documents`构建向量数据库

```python
from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import CharacterTextSplitter
from langchain_postgres import PGVector

connection = "postgresql+psycopg://langchain:langchain@9.135.138.110:5432/langchain"  # Uses psycopg3!
collection_name = "my_docs"

# load the documents, split them into chunks
loader = TextLoader("/trpc-agent/examples/agents/pgvector_rag_test.txt", encoding="utf-8")
documents = loader.load()
text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
docs = text_splitter.split_documents(documents)

# load embedding model
embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")

vectorstore = PGVector.from_documents(
        documents=docs,
        embedding=embedder,
        collection_name=collection_name,
        connection=connection,
        use_jsonb=True,
)
```

### 如何使用向量数据库进行检索

1) 使用`LangchainKnowledge`的成员方法`search`

```python
async def simple_search(query: str):
        # metadata it can be used to store metadata
        metadata = {
            'assistant_name' : 'test',  # Agent Name, 可用于上下文
            'runnable_config' : {},  # Langchain中的Runnable配置
        }
        ctx = new_agent_context(timeout=3000, metadata=metadata)
        sr: SearchRequest = SearchRequest()
        sr.query = Part.from_text(text = query)
        search_result: SearchResult = await rag.search(ctx, sr)
        best_doc = search_result.documents[0].document

        return {
                "status":
                "success",
                "report":
                f"content: {best_doc.page_content}"
        }
```

2) 使用`PGVector`的成员方法`similarity_search`

```python
results = vectorstore.similarity_search(
    query="LangChain provides abstractions to make working with LLMs easy",
    k=2,
    filter=[{"term": {"metadata.source.keyword": "tweet"}}],
)
for res in results:
    print(f"* {res.page_content} [{res.metadata}]")
```

### 如何使用向量数据库创建检索器

1) 使用`PGVector`的成员方法`as_retriever`获取检索器

```python
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

1) 创建`ElasticsearchStore`对象

```python
from langchain_elasticsearch import ElasticsearchStore
from langchain_huggingface import HuggingFaceEmbeddings

embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")

elastic_vector_search = ElasticsearchStore(
    es_url="http://X.X.X.X:X",
    index_name="langchain_index",
    embedding=embedder,
    es_api_key="UzVOc0o1a******************************",
)
```

2) 基于此vectorstore对象构造`LangchainKnowledge`对象

```python
rag = LangchainKnowledge(
    ...,
    vectorstore=elastic_vector_search,
    ...,
)
```

### 如何从文档构建向量数据库

如果是知识库已在Elasticsearch上构建好，则可跳过此部分，直接进行检索即可。否则，可以按如下步骤进行构建。我们支持如下方式：

1) 使用`LangchainKnowledge`成员方法`create_vectorstore_from_document`构建向量数据库

```python
import asyncio
import uuid

# Compatible imports for LangChain 0.3.x and 1.x.x
try:
    # langchain v1.x.x版本导入方式
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    # langchain v0.3.x版本导入方式
    from langchain.text_splitter import RecursiveCharacterTextSplitter

from langchain_community.document_loaders import TextLoader
from langchain_core.prompts import ChatPromptTemplate
from langchain_elasticsearch import ElasticsearchStore
from langchain_huggingface import HuggingFaceEmbeddings
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge

def build_chain():
    template = """Answer the question gently:
    Query: {query}
    """
    prompt = ChatPromptTemplate.from_template(template)
    embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")

    # create a ElasticsearchStore instance
    vectorstore = ElasticsearchStore(
        es_url="http://X.X.X.X:X",
        index_name="langchain_index",
        embedding=embedder,
        es_api_key="UzVOc0o1a******************************",
    )
    text_loader = TextLoader("/trpc-agent/examples/agents/elasticsearch_rag_test.txt", encoding="utf-8")
    text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)

    # use ElasticsearchStore to create a LangchainKnowledge instance
    rag = LangchainKnowledge(
        prompt_template=prompt,
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=embedder,
        vectorstore=vectorstore,
    )
    return rag

rag = build_chain()

async def create_vectorstore_from_document():
    await rag.create_vectorstore_from_document(
            es_url="http://X.X.X.X:X",
            index_name="langchain_index",
            es_api_key="UzVOc0o1a******************************",
        )

asyncio.run(create_vectorstore_from_document())
```

2) 使用`ElasticsearchStore`成员方法`add_documents`构建向量数据库

```python
import asyncio
import uuid

# Compatible imports for LangChain 0.3.x and 1.x.x
try:
    # langchain v1.x.x版本导入方式
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    # langchain v0.3.x版本导入方式
    from langchain.text_splitter import RecursiveCharacterTextSplitter

from langchain_community.document_loaders import TextLoader
from langchain_core.prompts import ChatPromptTemplate
from langchain_elasticsearch import ElasticsearchStore
from langchain_huggingface import HuggingFaceEmbeddings
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge

def build_chain():
    template = """Answer the question gently:
    Query: {query}
    """
    prompt = ChatPromptTemplate.from_template(template)
    embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")

    # create a ElasticsearchStore instance
    vectorstore = ElasticsearchStore(
        es_url="http://X.X.X.X:X",
        index_name="langchain_index",
        embedding=embedder,
        es_api_key="UzVOc0o1a******************************",
    )
    text_loader = TextLoader("/trpc-agent/examples/agents/elasticsearch_rag_test.txt", encoding="utf-8")
    text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)

    # use ElasticsearchStore to create a LangchainKnowledge instance
    rag = LangchainKnowledge(
        prompt_template=prompt,
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=embedder,
        vectorstore=vectorstore,
    )
    return rag

rag = build_chain()

async def create_vectorstore_from_document():
    # load documents
    documents = await rag.document_loader.aload()
    # split documents
    documents = await rag.document_transformer.atransform_documents(documents)
    uuids = [str(uuid.uuid4()) for _ in range(len(documents))]
    # add documents to vectorstore
    added_ids = await rag.vectorstore.aadd_documents(documents=documents, ids=uuids)
    return added_ids

asyncio.run(create_vectorstore_from_document())
```

3) 直接使用`ElasticsearchStore`的类方法`from_documents`构建向量数据库

```python
from langchain_community.document_loaders import TextLoader
from langchain_elasticsearch import ElasticsearchStore
from langchain_text_splitters import CharacterTextSplitter

# load the documents, split them into chunks
loader = TextLoader("/trpc-agent/examples/agents/elasticsearch_rag_test.txt", encoding="utf-8")
documents = loader.load()
text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
docs = text_splitter.split_documents(documents)

# load embedding model
embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")

vectorstore = ElasticsearchStore.from_documents(
    documents=docs,
    embedding=embedder,
    es_url="http://X.X.X.X:X",
    index_name="langchain_index",
    es_api_key="UzVOc0o1a******************************",
)
```

### 如何使用向量数据库进行检索

1) 使用`LangchainKnowledge`的成员方法`search`

```python
async def simple_search(query: str):
        # metadata it can be used to store metadata
        metadata = {
            'assistant_name' : 'test',  # Agent Name, 可用于上下文
            'runnable_config' : {},  # Langchain中的Runnable配置
        }
        ctx = new_agent_context(timeout=3000, metadata=metadata)
        sr: SearchRequest = SearchRequest()
        sr.query = Part.from_text(text = query)
        search_result: SearchResult = await rag.search(ctx, sr)
        best_doc = search_result.documents[0].document

        return {
                "status":
                "success",
                "report":
                f"content: {best_doc.page_content}"
        }
```

2) 使用`ElasticsearchStore`的成员方法`similarity_search`

```python
results = vectorstore.similarity_search(
    query="LangChain provides abstractions to make working with LLMs easy",
    k=2,
    filter=[{"term": {"metadata.source.keyword": "tweet"}}],
)
for res in results:
    print(f"* {res.page_content} [{res.metadata}]")
```

3) 设置检索策略：使用`ElasticsearchStore`的成员方法`from_documents`，通过参数`strategy`设置检索策略

```python
from langchain_elasticsearch import DenseVectorStrategy

vectorstore = ElasticsearchStore.from_documents(
    documents=docs,
    embedding=embedder,
    es_url="http://X.X.X.X:X",
    index_name="langchain_index",
    es_api_key="UzVOc0o1a******************************",
    strategy=DenseVectorStrategy(),
)

docs = vectorstore.similarity_search(
    query="What did the president say about Ketanji Brown Jackson?", k=10
)
```

说明：更多检索策略详见[LangChain Elasticsearch Retrieval Strategies](https://python.langchain.com/docs/integrations/vectorstores/elasticsearch/#retrieval-strategies)

### 如何使用向量数据库创建检索器

1) 使用`ElasticsearchStore`的成员方法`as_retriever`获取检索器

```python
retriever = vectorstore.as_retriever(
    search_type="similarity_score_threshold", search_kwargs={"score_threshold": 0.2}
)
retriever.invoke("Stealing from the bank is a crime")
```

2) 使用`ElasticsearchRetriever`的类方法`from_es_params`创建检索器

```python
from langchain_elasticsearch import ElasticsearchRetriever

def vector_query(search_query: str) -> Dict:
    vector = embeddings.embed_query(search_query)  # same embeddings as for indexing
    return {
        "knn": {
            "field": dense_vector_field,
            "query_vector": vector,
            "k": 5,
            "num_candidates": 10,
        }
    }

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

1) 创建`TencentVectorDB`对象

```python
from langchain_community.vectorstores.tencentvectordb import (
    ConnectionParams,
    TencentVectorDB,
)

conn_params = ConnectionParams(
    url="http://10.0.X.X",
    key="eC4bLRy2va******************************",
    username="root",
    timeout=20,
)

embeddings = None
t_vdb_embedding = "bge-base-zh"  # bge-base-zh is the default model
vectorstore = TencentVectorDB(embedding=embeddings, connection_params=conn_params, database_name="LangChainDatabase", collection_name="LangChainCollection", t_vdb_embedding=t_vdb_embedding)
```

2) 基于此vectorstore对象构造`LangchainKnowledge`对象

```python
rag = LangchainKnowledge(
    ...,
    vectorstore=vectorstore,
    ...,
)
```

### 如何从文档构建向量数据库

如果是知识库已在腾讯云向量数据库上构建好，则可跳过此部分，直接进行检索即可。否则，可以按如下步骤进行构建。我们支持如下方式：

1) 使用`LangchainKnowledge`类方法`create_vectorstore_from_document`构建向量数据库

```python
import asyncio

# Compatible imports for LangChain 0.3.x and 1.x.x
try:
    # langchain v1.x.x版本导入方式
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    # langchain v0.3.x版本导入方式
    from langchain.text_splitter import RecursiveCharacterTextSplitter

from langchain_community.document_loaders import TextLoader
from langchain_core.prompts import ChatPromptTemplate
from langchain_community.vectorstores.tencentvectordb import (
    ConnectionParams,
    TencentVectorDB,
)
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge

# create a TencentVectorDB instance from documents

# 腾讯云数据库连接参数
conn_params = ConnectionParams(
    url="http://10.0.X.X",
    key="eC4bLRy2va******************************",
    username="root",
    timeout=20,
)

# 腾讯云数据库索引参数，dimension为向量维度，replicas为副本数（默认为2，但这边测试时申请的数据库只有一个Node，故设置replicas为0）
index_params = IndexParams(dimension=768, replicas=0)

template = """Answer the question gently:
Query: {query}
"""
prompt = ChatPromptTemplate.from_template(template)
t_vdb_embedding = "bge-base-zh"  # bge-base-zh is the default model
embeddings = None
vectorstore = TencentVectorDB(embedding=embeddings, connection_params=conn_params, database_name="LangChainDatabase", collection_name="LangChainCollection", t_vdb_embedding=t_vdb_embedding)
text_loader = TextLoader("/trpc-agent/examples/agents/tencentvdb_rag_test.txt", encoding="utf-8")
# 这里需根据实际文本长度调整chunk_size和chunk_overlap
text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)
# 使用腾讯云向量数据库创建LangchainKnowledge
rag = LangchainKnowledge(
    prompt_template=prompt,
    document_loader=text_loader,
    document_transformer=text_splitter,
    embedder=embeddings,
    vectorstore=vectorstore,
)

async def create_vectorstore_from_document():
    """调用create_vectorstore_from_document接口构建数据库"""
    await rag.create_vectorstore_from_document(
        embeddings=embeddings,
        connection_params=connection_params,
        index_params=index_params,
        database_name="LangChainDatabase",
        collection_name="LangChainCollection",
        t_vdb_embedding=t_vdb_embedding,
    )

asyncio.run(create_vectorstore_from_document())
```

2) 直接使用`TencentVectorDB`的类方法`from_documents`构建向量数据库

```python
from langchain_community.document_loaders import TextLoader
from langchain_community.vectorstores.tencentvectordb import (
    ConnectionParams,
    TencentVectorDB,
)
from langchain_text_splitters import CharacterTextSplitter

# load the documents, split them into chunks.
loader = TextLoader("../../how_to/state_of_the_union.txt")
documents = loader.load()
text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
docs = text_splitter.split_documents(documents)

# create a TencentVectorDB instance from documents
conn_params = ConnectionParams(
    url="http://10.0.X.X",
    key="eC4bLRy2va******************************",
    username="root",
    timeout=20,
)

##  you can use a Langchain Embeddings model, like OpenAIEmbeddings:

# from langchain_community.embeddings.openai import OpenAIEmbeddings
#
# embeddings = OpenAIEmbeddings()
# t_vdb_embedding = None

## Or you can use a Tencent Embedding model, like `bge-base-zh`:

t_vdb_embedding = "bge-base-zh"  # bge-base-zh is the default model
embeddings = None

vector_db = TencentVectorDB.from_documents(
    docs, embeddings=embeddings, connection_params=conn_params, t_vdb_embedding=t_vdb_embedding
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

1) 使用`LangchainKnowledge`类方法`search`

```python
async def simple_search(query: str):
        # metadata 可用于存储元数据
        metadata = {
            'assistant_name' : 'test',  # Agent Name, 可用于上下文
            'runnable_config' : {},  # Langchain中的Runnable配置
        }
        ctx = new_agent_context(timeout=3000, metadata=metadata)
        sr: SearchRequest = SearchRequest()
        sr.query = Part.from_text(text = query)
        search_result: SearchResult = await rag.search(ctx, sr)
        if len(search_result.documents) == 0:
            return {"status": "failed", "report": "No documents found"}

        best_doc = search_result.documents[0].document
        return {"status": "success", "report": f"content: {best_doc.page_content}"}
```

2) 使用`TencentVectorDB`的类方法`similarity_search`

```python
query = "What did the president say about Ketanji Brown Jackson"
docs = vector_db.similarity_search(query)
print(docs[0].page_content)
```

### 示例

完整示例可见[tencentvdb_rag_agent](../../../examples/agents/tencentvdb_rag_agent.py)，

示例运行步骤：

1. 将示例脚本中的key和url参数替换成数据库实例的连接地址和密钥:

```python
connection_params = ConnectionParams(
    url="http://10.0.X.X",
    key="eC4bLRy2va******************************",
    ...
)
```

2. 放置待导入腾讯云数据库的文档

如知识库已在腾讯云向量数据库上构建好，则将`tencentvdb_rag_agent.py`示例脚本中的`run_rag_demo(query, True)`的第二个参数改成False，然后跳过此步骤。

否则将示例中的知识库文档"tencentvdb_rag_test.txt"换成待导入腾讯云向量数据库的知识库文档。亦或使用如下命令来生成用于测试的知识库文档：

```shell
echo "shenzhen weather: sunny
guangzhou weather: rain
shanghai weather: cloud" > test.txt
```

3. 执行示例脚本

```shell
python tencentvdb_rag_agent.py
```

待提示语"Input your question:"出现后，输入你的问题（如"shenzhen weather"），这时示例脚本会根据本地的知识库文档构建知识库(可选)，然后llmagent调用工具进行向量检索，再输出答案。

### 参考文档

- [LangChain Tencent Cloud VectorDB](https://python.langchain.com/docs/integrations/vectorstores/tencentvectordb/)
- [腾讯云向量数据库](https://cloud.tencent.com/document/product/1709)
