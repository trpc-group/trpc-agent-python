# VectorStore

The tRPC-Agent-Python framework supports multiple vector store backends through `LangchainKnowledge`. A VectorStore stores vectorized representations (embeddings) of text and supports efficient similarity-based retrieval. It is a core component for building RAG (Retrieval-Augmented Generation) applications. This document explains how to integrate and use the following vector backends in the framework:

- [PGVector](#pgvector)
- [Elasticsearch](#elasticsearch)
- [Tencent Cloud VectorDB](#tencent-cloud-vectordb)

## PGVector

### Install Dependencies

```bash
pip install -qU langchain-postgres
```

### Usage

Create a `PGVector` object and construct a `LangchainKnowledge`:

```python
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge

def _build_pgvector_knowledge() -> LangchainKnowledge:
    """Build knowledge with PGVector vectorstore"""
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_postgres import PGVector

    config = get_pgvector_config()
    # Initialize the embedding model to convert text into vector representations
    embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    # Create a PGVector instance; use_jsonb=True stores metadata in JSONB format, enabling structured filtering
    vectorstore = PGVector(
        embeddings=embedder,
        collection_name=config["collection_name"],
        connection=config["connection"],
        use_jsonb=True,
    )
    text_loader = TextLoader(KNOWLEDGE_FILE, encoding="utf-8")
    # chunk_size and chunk_overlap control document chunking granularity, affecting retrieval accuracy
    text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)

    # Assemble all components into LangchainKnowledge for unified document loading, splitting, vectorization, and retrieval
    return LangchainKnowledge(
        prompt_template=rag_prompt,
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=embedder,
        vectorstore=vectorstore,
    )
```

Note: Ensure that a PostgreSQL database with the pgvector extension is running. Connection parameters are configured via environment variables. See `get_pgvector_config()` in [`examples/knowledge_with_vectorstore/agent/config.py`](../../../examples/knowledge_with_vectorstore/agent/config.py) for details.

### How to Build a Vector Database from Documents

If the knowledge base has already been built on PGVector, you can skip this section and proceed directly to retrieval. Otherwise, follow the steps below to build it. The following methods are supported:

1) Use the `LangchainKnowledge` instance method `create_vectorstore_from_document` to build the vector store

```python
# examples/knowledge_with_vectorstore/run_agent.py
from agent.tools import rag

# Create the vector database from documents (skip this step if the knowledge base is already built)
await rag.create_vectorstore_from_document()
```

2) Use the `PGVector` instance method `add_documents` to add data to the vector store

Here is a simple example:

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

# Add documents to the vector database; ids are used for deduplication and subsequent updates
vectorstore.add_documents(docs, ids=[doc.metadata["id"] for doc in docs])
```

3) Use the `PGVector` class method `from_documents` to build the vector store directly

```python
from langchain_community.document_loaders import TextLoader
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import CharacterTextSplitter
from langchain_postgres import PGVector
from agent.config import get_pgvector_config

config = get_pgvector_config()

# Load and split documents
loader = TextLoader("test.txt", encoding="utf-8")
documents = loader.load()
text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
docs = text_splitter.split_documents(documents)

embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")

# from_documents performs vectorization and database insertion in one step, returning a queryable vectorstore instance
vectorstore = PGVector.from_documents(
    documents=docs,
    embedding=embedder,
    collection_name=config["collection_name"],
    connection=config["connection"],
    use_jsonb=True,
)
```

### How to Perform Retrieval Using the Vector Database

1) Use the `LangchainKnowledge` instance method `search`

```python
# examples/knowledge_with_vectorstore/agent/tools.py
async def simple_search(query: str):
    """Search the knowledge base for relevant documents"""
    metadata = {
        'assistant_name': 'test',
        'runnable_config': {},
    }
    # Create an Agent context; timeout sets the retrieval timeout in milliseconds
    ctx = new_agent_context(timeout=3000, metadata=metadata)
    # Wrap the query text as a SearchRequest
    sr: SearchRequest = SearchRequest()
    sr.query = Part.from_text(text=query)
    # Perform vector similarity retrieval, returning a list of documents sorted by relevance
    search_result: SearchResult = await rag.search(ctx, sr)
    if len(search_result.documents) == 0:
        return {"status": "failed", "report": "No documents found"}

    # Retrieve the top document with the highest similarity
    best_doc = search_result.documents[0].document
    return {"status": "success", "report": f"content: {best_doc.page_content}"}
```

2) Use the `PGVector` instance method `similarity_search`

```python
results = vectorstore.similarity_search(
    query="LangChain provides abstractions to make working with LLMs easy",
    k=2,                                                          # Return Top-K most similar results
    filter=[{"term": {"metadata.source.keyword": "tweet"}}],      # Filter based on metadata
)
for res in results:
    print(f"* {res.page_content} [{res.metadata}]")
```

### How to Create a Retriever from the Vector Database

Use the `PGVector` instance method `as_retriever` to obtain a retriever

```python
# search_type="mmr" uses the Maximal Marginal Relevance algorithm, balancing relevance and diversity
retriever = vectorstore.as_retriever(search_type="mmr", search_kwargs={"k": 1})
retriever.invoke("kitty")
```

### References

- [LangChain PGVector](https://python.langchain.com/docs/integrations/vectorstores/pgvector/)

---

## Elasticsearch

### Install Dependencies

```bash
pip install -qU langchain-elasticsearch
```

### Usage

Create an `ElasticsearchStore` object and construct a `LangchainKnowledge` (full code available in `examples/knowledge_with_vectorstore/agent/tools.py`):

```python
def _build_elasticsearch_knowledge() -> LangchainKnowledge:
    """Build knowledge with Elasticsearch vectorstore"""
    from langchain_elasticsearch import ElasticsearchStore
    from langchain_huggingface import HuggingFaceEmbeddings

    config = get_elasticsearch_config()
    embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    # Create an ElasticsearchStore instance, authenticating via es_api_key
    vectorstore = ElasticsearchStore(
        es_url=config["es_url"],
        index_name=config["index_name"],
        embedding=embedder,
        es_api_key=config["es_api_key"],
    )
    text_loader = TextLoader(KNOWLEDGE_FILE, encoding="utf-8")
    text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)

    # Assemble into LangchainKnowledge; usage is identical to PGVector, only the vectorstore backend differs
    return LangchainKnowledge(
        prompt_template=rag_prompt,
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=embedder,
        vectorstore=vectorstore,
    )
```

Note: Connection parameters are configured via environment variables. See `get_elasticsearch_config()` in [`examples/knowledge_with_vectorstore/agent/config.py`](../../../examples/knowledge_with_vectorstore/agent/config.py) for details.

### How to Build a Vector Database from Documents

If the knowledge base has already been built on Elasticsearch, you can skip this section and proceed directly to retrieval. Otherwise, follow the steps below to build it. The following methods are supported:

1) Use the `LangchainKnowledge` instance method `create_vectorstore_from_document` to build the vector store

```python
# examples/knowledge_with_vectorstore/run_agent.py
from agent.tools import rag, get_create_vectorstore_kwargs

# Elasticsearch requires additional connection parameters, automatically generated by get_create_vectorstore_kwargs()
await rag.create_vectorstore_from_document(**get_create_vectorstore_kwargs())
```

`get_create_vectorstore_kwargs()` returns the following parameters for Elasticsearch:
```python
def get_create_vectorstore_kwargs() -> dict:
    """Return extra kwargs for create_vectorstore_from_document based on vectorstore type"""
    vstore_type = get_vectorstore_type()
    # ...
    elif vstore_type == "elasticsearch":
        config = get_elasticsearch_config()
        # These parameters are passed to LangchainKnowledge.create_vectorstore_from_document()
        return {
            "es_url": config["es_url"],
            "index_name": config["index_name"],
            "es_api_key": config["es_api_key"],
        }
```

2) Use the `ElasticsearchStore` instance method `add_documents` to build the vector store

```python
import uuid
from agent.tools import rag

async def create_vectorstore_from_document():
    # Asynchronously load raw documents
    documents = await rag.document_loader.aload()
    # Split documents into smaller chunks according to the configured splitting strategy
    documents = await rag.document_transformer.atransform_documents(documents)
    # Generate a unique ID for each document chunk to enable deduplication and updates
    uuids = [str(uuid.uuid4()) for _ in range(len(documents))]
    # Asynchronously vectorize document chunks and write them to the vector database
    added_ids = await rag.vectorstore.aadd_documents(documents=documents, ids=uuids)
    return added_ids
```

3) Use the `ElasticsearchStore` class method `from_documents` to build the vector store directly

```python
from langchain_community.document_loaders import TextLoader
from langchain_elasticsearch import ElasticsearchStore
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import CharacterTextSplitter
from agent.config import get_elasticsearch_config

config = get_elasticsearch_config()

# Load and split documents
loader = TextLoader("test.txt", encoding="utf-8")
documents = loader.load()
text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
docs = text_splitter.split_documents(documents)

embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")

# from_documents performs vectorization and writes to Elasticsearch in one step, returning a queryable vectorstore instance
vectorstore = ElasticsearchStore.from_documents(
    documents=docs,
    embedding=embedder,
    es_url=config["es_url"],
    index_name=config["index_name"],
    es_api_key=config["es_api_key"],
)
```

### How to Perform Retrieval Using the Vector Database

1) Use the `LangchainKnowledge` instance method `search`

```python
# examples/knowledge_with_vectorstore/agent/tools.py
async def simple_search(query: str):
    """Search the knowledge base for relevant documents"""
    metadata = {
        'assistant_name': 'test',
        'runnable_config': {},
    }
    # Create an Agent context; timeout sets the retrieval timeout in milliseconds
    ctx = new_agent_context(timeout=3000, metadata=metadata)
    # Wrap the query text as a SearchRequest
    sr: SearchRequest = SearchRequest()
    sr.query = Part.from_text(text=query)
    # Perform vector similarity retrieval, returning a list of documents sorted by relevance
    search_result: SearchResult = await rag.search(ctx, sr)
    if len(search_result.documents) == 0:
        return {"status": "failed", "report": "No documents found"}

    # Retrieve the top document with the highest similarity
    best_doc = search_result.documents[0].document
    return {"status": "success", "report": f"content: {best_doc.page_content}"}
```

2) Use the `ElasticsearchStore` instance method `similarity_search`

```python
results = vectorstore.similarity_search(
    query="LangChain provides abstractions to make working with LLMs easy",
    k=2,                                                          # Return Top-K most similar results
    filter=[{"term": {"metadata.source.keyword": "tweet"}}],      # Filter based on metadata
)
for res in results:
    print(f"* {res.page_content} [{res.metadata}]")
```

3) Configure retrieval strategy: use the `ElasticsearchStore` class method `from_documents` with the `strategy` parameter to set the retrieval strategy

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
    strategy=DenseVectorStrategy(),  # Use dense vector retrieval strategy; alternatives include SparseVectorStrategy, etc.
)

docs = vectorstore.similarity_search(
    query="What did the president say about Ketanji Brown Jackson?", k=10
)
```

Note: For more retrieval strategies, see [LangChain Elasticsearch Retrieval Strategies](https://python.langchain.com/docs/integrations/vectorstores/elasticsearch/#retrieval-strategies)

### How to Create a Retriever from the Vector Database

1) Use the `ElasticsearchStore` instance method `as_retriever` to obtain a retriever

```python
# similarity_score_threshold only returns results with similarity above the threshold, filtering out low-quality matches
retriever = vectorstore.as_retriever(
    search_type="similarity_score_threshold", search_kwargs={"score_threshold": 0.2}
)
retriever.invoke("Stealing from the bank is a crime")
```

2) Use the `ElasticsearchRetriever` class method `from_es_params` to create a retriever

```python
from langchain_elasticsearch import ElasticsearchRetriever

# Custom vector query function that returns an Elasticsearch native KNN query DSL
def vector_query(search_query: str) -> Dict:
    vector = embeddings.embed_query(search_query)  # Use the same embedding model as during indexing
    return {
        "knn": {
            "field": dense_vector_field,
            "query_vector": vector,
            "k": 5,              # Return the 5 most similar results
            "num_candidates": 10, # Candidate set size; larger values increase accuracy but reduce performance
        }
    }

# Create a retriever via native ES parameters; body_func allows fully customized query logic
vector_retriever = ElasticsearchRetriever.from_es_params(
    index_name=index_name,
    body_func=vector_query,
    content_field=text_field,
    url=es_url,
)

vector_retriever.invoke("foo")
```

Note: For more retriever types, see [LangChain Elasticsearch Retriever](https://python.langchain.com/docs/integrations/retrievers/elasticsearch_retriever/)

### References

- [LangChain Elasticsearch](https://python.langchain.com/docs/integrations/vectorstores/elasticsearch/)
- [LangChain Elasticsearch Retriever](https://python.langchain.com/docs/integrations/retrievers/elasticsearch_retriever/)

---

## Tencent Cloud VectorDB

### Install Dependencies

```bash
pip3 install tcvectordb langchain-community
```

### Usage

Create a `TencentVectorDB` object and construct a `LangchainKnowledge`:

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
    # dimension must match the output dimension of the embedding model being used
    index_params = IndexParams(dimension=768, replicas=0)
    # Tencent Cloud VectorDB supports server-side built-in embedding, so no external embedding model is needed; pass None
    embeddings = None
    vectorstore = TencentVectorDB(
        embedding=embeddings,
        connection_params=connection_params,
        index_params=index_params,
        database_name=config["database_name"],
        collection_name=config["collection_name"],
        t_vdb_embedding=config["t_vdb_embedding"],  # Specify the server-side built-in embedding model
    )
    text_loader = TextLoader(KNOWLEDGE_FILE, encoding="utf-8")
    text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)

    # Pass None for embedder since vectorization is handled by the Tencent Cloud server
    return LangchainKnowledge(
        prompt_template=rag_prompt,
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=None,
        vectorstore=vectorstore,
    )
```

Note: Connection parameters are configured via environment variables. See `get_tencentvdb_config()` in [`examples/knowledge_with_vectorstore/agent/config.py`](../../../examples/knowledge_with_vectorstore/agent/config.py) for details.

### How to Build a Vector Database from Documents

If the knowledge base has already been built on Tencent Cloud VectorDB, you can skip this section and proceed directly to retrieval. Otherwise, follow the steps below to build it. The following methods are supported:

1) Use the `LangchainKnowledge` instance method `create_vectorstore_from_document` to build the vector store

```python
# examples/knowledge_with_vectorstore/run_agent.py
from agent.tools import rag, get_create_vectorstore_kwargs

# Tencent Cloud VectorDB requires additional connection parameters, automatically generated by get_create_vectorstore_kwargs()
await rag.create_vectorstore_from_document(**get_create_vectorstore_kwargs())
```

`get_create_vectorstore_kwargs()` returns the following parameters for Tencent Cloud VectorDB:

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
        # These parameters are passed to LangchainKnowledge.create_vectorstore_from_document()
        # to create a vector database collection on the Tencent Cloud server
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

2) Use the `TencentVectorDB` class method `from_documents` to build the vector store directly

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

# Use server-side built-in embedding; no local embedding model required
embeddings = None

vector_db = TencentVectorDB.from_documents(
    docs,
    embeddings=embeddings,
    connection_params=conn_params,
    t_vdb_embedding=config["t_vdb_embedding"],  # Specify the server-side embedding model name
)
```

Note: `from_documents` internally calls the `from_texts` interface, which supports additional parameters (such as database name, connection name, etc.). See the `from_texts` interface definition for details:

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

3) Use the `TencentVectorDB` instance method `add_texts` to insert data into the vector store

```python
# If document IDs are not specified, they will be randomly generated. You can specify them via the ids: Optional[List[str]] parameter
vector_db.add_texts(["Ankush went to Princeton"])
```

### How to Perform Retrieval Using the Vector Database

1) Use the `LangchainKnowledge` instance method `search`

```python
# examples/knowledge_with_vectorstore/agent/tools.py
async def simple_search(query: str):
    """Search the knowledge base for relevant documents"""
    metadata = {
        'assistant_name': 'test',
        'runnable_config': {},
    }
    # Create an Agent context; timeout sets the retrieval timeout in milliseconds
    ctx = new_agent_context(timeout=3000, metadata=metadata)
    # Wrap the query text as a SearchRequest
    sr: SearchRequest = SearchRequest()
    sr.query = Part.from_text(text=query)
    # Perform vector similarity retrieval, returning a list of documents sorted by relevance
    search_result: SearchResult = await rag.search(ctx, sr)
    if len(search_result.documents) == 0:
        return {"status": "failed", "report": "No documents found"}

    # Retrieve the top document with the highest similarity
    best_doc = search_result.documents[0].document
    return {"status": "success", "report": f"content: {best_doc.page_content}"}
```

2) Use the `TencentVectorDB` instance method `similarity_search`

```python
query = "What did the president say about Ketanji Brown Jackson"
docs = vector_db.similarity_search(query)
print(docs[0].page_content)
```

### References

- [LangChain Tencent Cloud VectorDB](https://python.langchain.com/docs/integrations/vectorstores/tencentvectordb/)
- [Tencent Cloud VectorDB](https://cloud.tencent.com/document/product/1709)

## Complete Example

The complete example is available at [knowledge_with_vectorstore](../../../examples/knowledge_with_vectorstore/), supporting three backends: PGVector, Elasticsearch, and Tencent Cloud VectorDB.

Steps to run the example:

1. Configure environment variables in `examples/knowledge_with_vectorstore/.env`, set `VECTORSTORE_TYPE=tencentvdb` and fill in the Tencent Cloud VectorDB connection parameters:

```bash
VECTORSTORE_TYPE=tencentvdb
TENCENT_VDB_URL=http://10.0.X.X
TENCENT_VDB_KEY=your_key_here
TENCENT_VDB_USERNAME=root
TENCENT_VDB_DATABASE=LangChainDatabase
TENCENT_VDB_COLLECTION=LangChainCollection
TENCENT_VDB_EMBEDDING=bge-base-zh
```

2. Prepare the knowledge base documents

The project uses `test.txt` as the default knowledge base document. You can replace it with your own document or specify a custom path via the `KNOWLEDGE_FILE` environment variable:

```shell
echo "shenzhen weather: sunny
guangzhou weather: rain
shanghai weather: cloud" > test.txt
```

3. Run the example

```shell
cd examples/knowledge_with_vectorstore/
python3 run_agent.py
```

The program automatically builds the vector store from documents. The Agent then receives queries and invokes the `simple_search` tool for vector retrieval, generating answers from the retrieved results.
