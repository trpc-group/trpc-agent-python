# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""MCP Tool types.
"""

from __future__ import annotations

from typing import TypeAlias

from mcp import StdioServerParameters as McpStdioServerParameters
from mcp.client.session_group import SseServerParameters as McpSseServerParameters
from mcp.client.session_group import StreamableHttpParameters as McpStreamableHttpParameters
from pydantic import BaseModel

DEFAULT_TIMEOUT = 5.0


class StdioConnectionParams(BaseModel):
    """Parameters for the MCP Stdio connection.

    Attributes:
        server_params: Parameters for the MCP Stdio server.
        timeout: Timeout in seconds for establishing the connection to the MCP
          stdio server.
    """

    server_params: McpStdioServerParameters
    timeout: float = DEFAULT_TIMEOUT


StreamableHTTPConnectionParams: TypeAlias = McpStreamableHttpParameters
SseConnectionParams: TypeAlias = McpSseServerParameters

McpConnectionParamsType: TypeAlias = SseConnectionParams | StreamableHTTPConnectionParams | StdioConnectionParams | None
