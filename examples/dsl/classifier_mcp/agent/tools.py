# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool helpers for generated graph workflow."""

import os
from typing import Any

from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.tools import SseConnectionParams


def create_tools_llmagent2() -> list[Any]:
    tools: list[Any] = []
    connection_params_1_url = os.getenv('MCP1_SERVER_URL')
    if not connection_params_1_url:
        raise ValueError("MCP server_url is empty")
    connection_params_1 = SseConnectionParams(
        url=connection_params_1_url,
        timeout=30.0,
    )
    tools.append(MCPToolset(connection_params=connection_params_1))
    return tools


def create_tools_llmagent3() -> list[Any]:
    tools: list[Any] = []
    connection_params_1_url = os.getenv('MCP2_SERVER_URL')
    if not connection_params_1_url:
        raise ValueError("MCP server_url is empty")
    connection_params_1 = SseConnectionParams(
        url=connection_params_1_url,
        timeout=30.0,
    )
    tools.append(MCPToolset(connection_params=connection_params_1))
    return tools
