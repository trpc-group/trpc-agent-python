# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Agent config module"""

import os


def get_model_config() -> tuple[str, str, str]:
    """Get model config from environment variables"""
    api_key = os.getenv('TRPC_AGENT_API_KEY', os.getenv('API_KEY', ''))
    url = os.getenv('TRPC_AGENT_BASE_URL', 'http://v2.open.venus.woa.com/llmproxy')
    model_name = os.getenv('TRPC_AGENT_MODEL_NAME', 'deepseek-v3-local-II')
    return api_key, url, model_name
