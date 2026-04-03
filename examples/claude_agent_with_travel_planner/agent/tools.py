# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Tools for the agent. """

import datetime

from mcp import StdioServerParameters
from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.tools.mcp_tool import StdioConnectionParams


# 工具环境安装请参考: https://knot.woa.com/mcp/detail/39
# 1. (可选)安装uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. 安装mcp: uv pip install duckduckgo-mcp-server
class DuckDuckGoSearchMCP(MCPToolset):
    """DuckDuckGoSearchMCP 搜索工具集"""

    def __init__(self):
        super().__init__()
        self._connection_params = StdioConnectionParams(
            server_params=StdioServerParameters(
                command="uvx",
                args=["duckduckgo-mcp-server"],
                env=None,
            ),
            timeout=10.0,
        )


def get_current_date():
    """获取今天的日期，日期格式为：2025-01-01"""
    return datetime.datetime.now().strftime("%Y-%m-%d")
