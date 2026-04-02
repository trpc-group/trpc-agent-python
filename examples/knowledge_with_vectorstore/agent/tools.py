# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
""" Tools for the agent. """

import os

# Compatible imports for LangChain 0.3.x and 1.x.x
try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import TextLoader

from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.knowledge import SearchRequest, SearchResult
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.server.knowledge.langchain_knowledge import LangchainKnowledge

from .config import (
    get_elasticsearch_config,
    get_pgvector_config,
    get_tencentvdb_config,
    get_vectorstore_type,
)
from .prompts import rag_prompt

KNOWLEDGE_FILE = os.getenv(
    "KNOWLEDGE_FILE",
    os.path.join(os.path.dirname(__file__), "..", "test.txt"),
)


def _build_pgvector_knowledge() -> LangchainKnowledge:
    """Build knowledge with PGVector vectorstore"""
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_postgres import PGVector

    config = get_pgvector_config()
    embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    vectorstore = PGVector(
        embeddings=embedder,
        collection_name=config["collection_name"],
        connection=config["connection"],
        use_jsonb=True,
    )
    text_loader = TextLoader(KNOWLEDGE_FILE, encoding="utf-8")
    text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)

    return LangchainKnowledge(
        prompt_template=rag_prompt,
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=embedder,
        vectorstore=vectorstore,
    )


def _build_elasticsearch_knowledge() -> LangchainKnowledge:
    """Build knowledge with Elasticsearch vectorstore"""
    from langchain_elasticsearch import ElasticsearchStore
    from langchain_huggingface import HuggingFaceEmbeddings

    config = get_elasticsearch_config()
    embedder = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    vectorstore = ElasticsearchStore(
        es_url=config["es_url"],
        index_name=config["index_name"],
        embedding=embedder,
        es_api_key=config["es_api_key"],
    )
    text_loader = TextLoader(KNOWLEDGE_FILE, encoding="utf-8")
    text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)

    return LangchainKnowledge(
        prompt_template=rag_prompt,
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=embedder,
        vectorstore=vectorstore,
    )


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
    index_params = IndexParams(dimension=768, replicas=0)
    embeddings = None
    vectorstore = TencentVectorDB(
        embedding=embeddings,
        connection_params=connection_params,
        index_params=index_params,
        database_name=config["database_name"],
        collection_name=config["collection_name"],
        t_vdb_embedding=config["t_vdb_embedding"],
    )
    text_loader = TextLoader(KNOWLEDGE_FILE, encoding="utf-8")
    text_splitter = RecursiveCharacterTextSplitter(separators=["\n"], chunk_size=10, chunk_overlap=0)

    return LangchainKnowledge(
        prompt_template=rag_prompt,
        document_loader=text_loader,
        document_transformer=text_splitter,
        embedder=None,
        vectorstore=vectorstore,
    )


_BUILDERS = {
    "pgvector": _build_pgvector_knowledge,
    "elasticsearch": _build_elasticsearch_knowledge,
    "tencentvdb": _build_tencentvdb_knowledge,
}


def build_knowledge() -> LangchainKnowledge:
    """Build the RAG knowledge chain based on VECTORSTORE_TYPE"""
    vstore_type = get_vectorstore_type()
    return _BUILDERS[vstore_type]()


rag = build_knowledge()


def get_create_vectorstore_kwargs() -> dict:
    """Return extra kwargs for create_vectorstore_from_document based on vectorstore type"""
    vstore_type = get_vectorstore_type()
    if vstore_type == "tencentvdb":
        from langchain_community.vectorstores.tencentvectordb import (
            ConnectionParams,
            IndexParams,
        )
        config = get_tencentvdb_config()
        return {
            "embeddings":
            None,
            "connection_params":
            ConnectionParams(
                url=config["url"],
                key=config["key"],
                username=config["username"],
                timeout=20,
            ),
            "index_params":
            IndexParams(dimension=768, replicas=0),
            "database_name":
            config["database_name"],
            "collection_name":
            config["collection_name"],
            "t_vdb_embedding":
            config["t_vdb_embedding"],
        }
    elif vstore_type == "pgvector":
        config = get_pgvector_config()
        return {
            "collection_name": config["collection_name"],
            "connection": config["connection"],
            "use_jsonb": True,
        }
    elif vstore_type == "elasticsearch":
        config = get_elasticsearch_config()
        return {
            "es_url": config["es_url"],
            "index_name": config["index_name"],
            "es_api_key": config["es_api_key"],
        }
    else:
        raise ValueError(f"Unsupported vectorstore type: {vstore_type}")


async def simple_search(query: str):
    """Search the knowledge base for relevant documents"""
    metadata = {
        'assistant_name': 'test',
        'runnable_config': {},
    }
    ctx = new_agent_context(timeout=3000, metadata=metadata)
    sr: SearchRequest = SearchRequest()
    sr.query = Part.from_text(text=query)
    search_result: SearchResult = await rag.search(ctx, sr)
    if len(search_result.documents) == 0:
        return {"status": "failed", "report": "No documents found"}

    best_doc = search_result.documents[0].document
    return {"status": "success", "report": f"content: {best_doc.page_content}"}
