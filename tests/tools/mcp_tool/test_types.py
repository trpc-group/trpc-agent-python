# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import pytest
from mcp import StdioServerParameters as McpStdioServerParameters
from mcp.client.session_group import SseServerParameters as McpSseServerParameters
from mcp.client.session_group import StreamableHttpParameters as McpStreamableHttpParameters

from trpc_agent_sdk.tools.mcp_tool._types import (
    DEFAULT_TIMEOUT,
    McpConnectionParamsType,
    SseConnectionParams,
    StdioConnectionParams,
    StreamableHTTPConnectionParams,
)


class TestDefaultTimeout:
    def test_default_timeout_value(self):
        assert DEFAULT_TIMEOUT == 5.0


class TestStdioConnectionParams:
    def test_create_with_server_params(self):
        server_params = McpStdioServerParameters(command="npx", args=["-y", "server"])
        conn = StdioConnectionParams(server_params=server_params)

        assert conn.server_params == server_params
        assert conn.timeout == DEFAULT_TIMEOUT

    def test_create_with_custom_timeout(self):
        server_params = McpStdioServerParameters(command="python3", args=["server.py"])
        conn = StdioConnectionParams(server_params=server_params, timeout=10.0)

        assert conn.timeout == 10.0

    def test_missing_server_params_raises(self):
        with pytest.raises(Exception):
            StdioConnectionParams()


class TestTypeAliases:
    def test_streamable_http_is_mcp_alias(self):
        assert StreamableHTTPConnectionParams is McpStreamableHttpParameters

    def test_sse_is_mcp_alias(self):
        assert SseConnectionParams is McpSseServerParameters


class TestMcpConnectionParamsType:
    def test_stdio_is_valid(self):
        server_params = McpStdioServerParameters(command="npx")
        conn = StdioConnectionParams(server_params=server_params)
        assert isinstance(conn, StdioConnectionParams)

    def test_none_is_valid(self):
        value: McpConnectionParamsType = None
        assert value is None
