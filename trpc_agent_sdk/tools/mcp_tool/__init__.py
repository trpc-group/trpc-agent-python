# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""MCP tool module for TRPC Agent framework."""

from ._mcp_session_manager import MCPSessionManager
from ._mcp_tool import MCPTool
from ._mcp_toolset import MCPToolset
from ._types import McpConnectionParamsType
from ._types import McpStdioServerParameters
from ._types import SseConnectionParams
from ._types import StdioConnectionParams
from ._types import StreamableHTTPConnectionParams
from ._utils import convert_conn_params
from ._utils import patch_mcp_cancel_scope_exit_issue

__all__ = [
    "MCPSessionManager",
    "MCPTool",
    "MCPToolset",
    "McpConnectionParamsType",
    "McpStdioServerParameters",
    "SseConnectionParams",
    "StdioConnectionParams",
    "StreamableHTTPConnectionParams",
    "convert_conn_params",
    "patch_mcp_cancel_scope_exit_issue",
]
