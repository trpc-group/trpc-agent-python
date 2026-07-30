#!/usr/bin/env python3
#
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Local stdio MCP server for the Tool Script Safety Guard demo."""

from __future__ import annotations

import json

from mcp.server import FastMCP

app = FastMCP("tool-safety-demo-mcp")


@app.tool()
async def run_shell_command(command: str) -> str:
    """Receive a shell command through MCP and return a dry-run record.

    The MCP server intentionally does not execute the command. The real security
    boundary demonstrated here is the MCPTool filter: denied commands should be
    blocked before this server receives them.
    """
    return json.dumps(
        {
            "mcp_server": "tool-safety-demo-mcp",
            "received_command": command,
            "executed": False,
            "note": "dry-run MCP endpoint; safety decision happened before this call",
        },
        ensure_ascii=False,
    )


if __name__ == "__main__":
    app.run(transport="stdio")
