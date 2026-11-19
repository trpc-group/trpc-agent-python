# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Environment configuration for the DSH agent example."""

import os

from trpc_agent_sdk.server.agents.dsh import DSHConfig
from trpc_agent_sdk.server.agents.dsh import DSHModel


def get_model_config() -> tuple[str, str, str]:
    """Read the OpenAI-compatible gateway settings from the environment."""

    api_key = os.getenv("TRPC_AGENT_API_KEY", "")
    url = os.getenv("TRPC_AGENT_BASE_URL", "")
    model_name = os.getenv("TRPC_AGENT_MODEL_NAME", "")
    if not api_key or not url or not model_name:
        raise ValueError(
            "TRPC_AGENT_API_KEY, TRPC_AGENT_BASE_URL and TRPC_AGENT_MODEL_NAME "
            "must be set (see examples/dsh_agent/.env)")
    return api_key, url, model_name


def get_dsh_conf() -> DSHConfig:
    """Build the DSH launch and model configuration."""

    api_key, url, model_name = get_model_config()
    return DSHConfig(
        profile="custom",
        dsh_home="dsh-home",
        model=DSHModel(
            provider="openai",
            model=model_name,
            base_url=url,
            api_key=api_key,
        ),
    )
