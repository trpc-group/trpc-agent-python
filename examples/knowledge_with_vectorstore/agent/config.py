# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Agent config module"""

import os


def get_model_config() -> tuple[str, str, str]:
    """Get model config from environment variables"""
    api_key = os.getenv('TRPC_AGENT_API_KEY', '')
    url = os.getenv('TRPC_AGENT_BASE_URL', '')
    model_name = os.getenv('TRPC_AGENT_MODEL_NAME', '')
    if not api_key or not url or not model_name:
        raise ValueError("TRPC_AGENT_API_KEY, TRPC_AGENT_BASE_URL, "
                         "and TRPC_AGENT_MODEL_NAME must be set in environment variables")
    return api_key, url, model_name


def get_vectorstore_type() -> str:
    """Get vectorstore type from environment variables.
    Supported: pgvector, elasticsearch, tencentvdb
    """
    vstore_type = os.getenv('VECTORSTORE_TYPE', '')
    if not vstore_type:
        raise ValueError("VECTORSTORE_TYPE must be set in environment variables")
    if vstore_type not in ('pgvector', 'elasticsearch', 'tencentvdb'):
        raise ValueError(f"Unsupported VECTORSTORE_TYPE: {vstore_type}. "
                         "Supported: pgvector, elasticsearch, tencentvdb")
    return vstore_type


def get_pgvector_config() -> dict:
    """Get PGVector config from environment variables"""
    connection = os.getenv('PGVECTOR_CONNECTION', '')
    collection_name = os.getenv('PGVECTOR_COLLECTION_NAME', 'my_docs')
    if not connection:
        raise ValueError("PGVECTOR_CONNECTION must be set in environment variables")
    return {
        "connection": connection,
        "collection_name": collection_name,
    }


def get_elasticsearch_config() -> dict:
    """Get Elasticsearch config from environment variables"""
    es_url = os.getenv('ES_URL', '')
    index_name = os.getenv('ES_INDEX_NAME', 'langchain_index')
    es_api_key = os.getenv('ES_API_KEY', '')
    if not es_url:
        raise ValueError("ES_URL must be set in environment variables")
    return {
        "es_url": es_url,
        "index_name": index_name,
        "es_api_key": es_api_key,
    }


def get_tencentvdb_config() -> dict:
    """Get Tencent Cloud VectorDB config from environment variables"""
    url = os.getenv('TENCENT_VDB_URL', '')
    key = os.getenv('TENCENT_VDB_KEY', '')
    username = os.getenv('TENCENT_VDB_USERNAME', 'root')
    database_name = os.getenv('TENCENT_VDB_DATABASE', 'LangChainDatabase')
    collection_name = os.getenv('TENCENT_VDB_COLLECTION', 'LangChainCollection')
    t_vdb_embedding = os.getenv('TENCENT_VDB_EMBEDDING', 'bge-base-zh')
    if not url or not key:
        raise ValueError("TENCENT_VDB_URL and TENCENT_VDB_KEY must be set in environment variables")
    return {
        "url": url,
        "key": key,
        "username": username,
        "database_name": database_name,
        "collection_name": collection_name,
        "t_vdb_embedding": t_vdb_embedding,
    }
