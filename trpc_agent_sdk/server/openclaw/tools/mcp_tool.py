# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# This file is part of tRPC-Agent-Python and is licensed under Apache-2.0.
#
# Portions of this file are derived from HKUDS/nanobot (MIT License):
# https://github.com/HKUDS/nanobot.git
#
# Copyright (c) 2025 nanobot contributors
#
# See the project LICENSE / third-party attribution notices for details.
#
"""MCP tool for trpc-claw."""
from typing import Any
from typing import Optional

from trpc_agent_sdk.log import logger
from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.tools import McpStdioServerParameters
from trpc_agent_sdk.tools import SseConnectionParams
from trpc_agent_sdk.tools import StdioConnectionParams
from trpc_agent_sdk.tools import StreamableHTTPConnectionParams
from trpc_agent_sdk.tools import patch_mcp_cancel_scope_exit_issue


def build_mcp_toolsets(mcp_servers: Optional[dict[str, Any]] = None, ) -> list[MCPToolset]:
    """Build MCP toolsets from trpc-claw-style mcp_servers config.

    Expected input shape (per server):
      {
        "type": "stdio" | "sse" | "streamableHttp",
        "command": "...", "args": [...], "env": {...},
        "url": "...", "headers": {...}, "tool_timeout": 30
      }
    """
    if not mcp_servers:
        return []

    patch_mcp_cancel_scope_exit_issue()
    toolsets: list[MCPToolset] = []

    server_configs = mcp_servers.values() if mcp_servers else []
    for name, cfg in server_configs:
        if cfg is None:
            logger.warning("MCP server config is None for server %s", name)
            continue

        # Support either dict or pydantic-like objects
        data = cfg.model_dump() if hasattr(cfg, "model_dump") else dict(cfg)
        server_type = (data.get("type") or "").strip().lower()
        timeout = float(data.get("tool_timeout") or 30)

        if server_type == "stdio" or (not server_type and data.get("command")):
            command = data.get("command", "")
            if not command:
                continue
            server_params = McpStdioServerParameters(
                command=command,
                args=data.get("args") or [],
                env=data.get("env") or {},
            )
            connection_params = StdioConnectionParams(
                server_params=server_params,
                timeout=timeout,
            )
        elif server_type == "sse":
            url = data.get("url", "")
            if not url:
                continue
            connection_params = SseConnectionParams(
                url=url,
                headers=data.get("headers") or {},
                timeout=timeout,
            )
        elif server_type == "streamablehttp":
            url = data.get("url", "")
            if not url:
                continue
            connection_params = StreamableHTTPConnectionParams(
                url=url,
                headers=data.get("headers") or {},
                timeout=timeout,
            )
        else:
            # Unknown type: skip silently to keep startup resilient.
            continue

        toolsets.append(MCPToolset(connection_params=connection_params))

    return toolsets
