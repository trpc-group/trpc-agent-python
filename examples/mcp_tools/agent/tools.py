# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" MCP Toolset definitions for the agent. """

import os
import sys

from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.tools import McpStdioServerParameters
from trpc_agent_sdk.tools import SseConnectionParams
from trpc_agent_sdk.tools import StdioConnectionParams
from trpc_agent_sdk.tools import StreamableHTTPConnectionParams


class StdioMCPToolset(MCPToolset):
    """Stdio-based MCP toolset that auto-launches the MCP server as a subprocess.

    The agent communicates with the MCP server over stdin/stdout. The server
    process is started automatically using the current Python interpreter.
    """

    def __init__(self):
        super().__init__()
        # Inherit current environment and ensure the Python shared library is discoverable
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = (f"/usr/local/Python{sys.version_info.major}{sys.version_info.minor}/lib/:" +
                                  env.get("LD_LIBRARY_PATH", ""))

        # Resolve the path to the MCP server script (located one level up)
        svr_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "mcp_server.py"))

        # Configure stdio transport: launch the server as a child process
        stdio_server_params = McpStdioServerParameters(
            command=f"python{sys.version_info.major}.{sys.version_info.minor}",
            args=[svr_file],
            env=env,
        )
        self._connection_params = StdioConnectionParams(
            server_params=stdio_server_params,
            timeout=5,
        )
        # Uncomment to expose only specific tools instead of all:
        # self._tool_filter = ["get_weather", "calculate"]


class SseMCPToolset(MCPToolset):
    """SSE-based MCP toolset that connects to a remote MCP server via Server-Sent Events.

    Suitable for scenarios where the MCP server is already running as a
    standalone HTTP service. The agent receives tool results through an SSE stream.
    """

    def __init__(self):
        super().__init__()
        self._connection_params = SseConnectionParams(
            url="http://localhost:8000/sse",
            headers={"Authorization": "Bearer token"},
            timeout=5,
            sse_read_timeout=60 * 5,  # keep the SSE connection alive for 5 minutes
        )


class StreamableHttpMCPToolset(MCPToolset):
    """Streamable-HTTP MCP toolset for bidirectional streaming over HTTP.

    Uses the Streamable HTTP transport, which supports bidirectional
    communication and automatic session cleanup on close.
    """

    def __init__(self):
        super().__init__()
        self._connection_params = StreamableHTTPConnectionParams(
            url="http://localhost:8000/mcp",
            headers={"Authorization": "Bearer <token>"},
            timeout=5,
            sse_read_timeout=60 * 5,
            terminate_on_close=True,  # send termination signal when the toolset is closed
        )
