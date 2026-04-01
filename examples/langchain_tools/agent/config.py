# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
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


def get_tavily_api_key() -> str:
    """Get Tavily API key from environment variables"""
    api_key = os.getenv('TAVILY_API_KEY', '')
    if not api_key:
        raise ValueError('TAVILY_API_KEY must be set in environment variables')
    return api_key
