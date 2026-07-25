#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Dry-run MCP server — safety decision happens at MCPTool filter layer.

This server intentionally does NOT execute received commands. It serves as a
proof point that the MCPTool filter runs before the request reaches the MCP
server. Denied commands are blocked at the filter and never arrive here.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

app = FastMCP("tool-safety-demo-mcp")


@app.tool()
async def run_shell_command(command: str) -> str:
    """Receive a shell command through MCP and return a dry-run record.

    The MCP server does not execute the command. The real security boundary
    is the MCPTool filter — denied commands are blocked before this server
    receives them.
    """
    return json.dumps(
        {
            "mcp_server": "tool-safety-demo-mcp",
            "received_command": command,
            "executed": False,
            "note": "Safety decision happened at MCPTool filter before this call",
        },
        ensure_ascii=False,
    )


if __name__ == "__main__":
    app.run(transport="stdio")
