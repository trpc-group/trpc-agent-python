# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Model configuration helpers for generated graph workflow."""

import os
from typing import Any

from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.tools import SseConnectionParams


def create_openai_model(
    model_name: str | None,
    api_key: str | None,
    base_url: str | None,
    headers: dict[str, str] | None = None,
) -> OpenAIModel:
    kwargs: dict[str, Any] = {}
    if headers:
        kwargs["client_args"] = {"default_headers": headers}
    return OpenAIModel(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        **kwargs,
    )


def create_model_llmagent1() -> OpenAIModel:
    model_name = os.getenv('MODEL1_NAME')
    api_key = os.getenv('MODEL1_API_KEY')
    base_url = os.getenv('MODEL1_BASE_URL')
    return create_openai_model(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        headers={},
    )


def create_mcp_toolset_mcp1() -> MCPToolset:
    server_url = os.getenv('MCP1_SERVER_URL')
    if not server_url:
        raise ValueError("MCP server_url is empty for node " + 'mcp_weather')

    connection_params = SseConnectionParams(
        url=server_url,
        timeout=30.0,
    )
    return MCPToolset(connection_params=connection_params)
