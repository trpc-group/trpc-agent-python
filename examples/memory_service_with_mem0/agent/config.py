# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Agent config module"""

import os

from mem0.configs.base import MemoryConfig


def get_model_config() -> tuple[str, str, str]:
    """Get model config from environment variables"""
    api_key = os.getenv('TRPC_AGENT_API_KEY', '')
    url = os.getenv('TRPC_AGENT_BASE_URL', '')
    model_name = os.getenv('TRPC_AGENT_MODEL_NAME', '')
    if not api_key or not url or not model_name:
        raise ValueError('''TRPC_AGENT_API_KEY, TRPC_AGENT_BASE_URL,
                         and TRPC_AGENT_MODEL_NAME must be set in environment variables''')
    return api_key, url, model_name


def get_memory_config() -> MemoryConfig:
    """Get memory config from environment variables"""
    memory_config = {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "host": "localhost",
                "port": 6333,
                "collection_name": "mem0",
            }
        },
        "llm": {
            "provider": "deepseek",
            "config": {
                "model": os.getenv('TRPC_AGENT_MODEL_NAME', ''),
                "api_key": os.getenv('TRPC_AGENT_API_KEY', ''),
                "deepseek_base_url": os.getenv('TRPC_AGENT_BASE_URL', ''),
                "temperature": 0.2,
                "max_tokens": 2000,
            }
        },
        "embedder": {
            "provider": "huggingface",
            "config": {
                "model": "multi-qa-MiniLM-L6-cos-v1"  # 本地运行，无需 API key
                # "model": "text-embedding-3-small"  # 需要 API key
            }
        }
    }
    return MemoryConfig(**memory_config)


def get_mem0_platform_config() -> dict:
    """Get mem0 platform config from environment variables"""
    return {
        "api_key": os.getenv('MEM0_API_KEY', ''),
        "host": os.getenv('MEM0_BASE_URL', ''),
    }
