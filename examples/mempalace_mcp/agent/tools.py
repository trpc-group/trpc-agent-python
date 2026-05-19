# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""MCP toolset that connects to the official MemPalace MCP server over stdio.

MemPalace ships an MCP server with ~30 tools (palace read/write, knowledge
graph, agent diary, navigation). The server is launched on demand by the
`MCPToolset` as a child process.

Why `python -m mempalace.mcp_server` is the default
---------------------------------------------------
In current MemPalace releases the CLI entry point that actually runs the
server is `mempalace-mcp` (with a hyphen). `mempalace mcp` (with a space) only
prints setup help — it does NOT start the server. To avoid relying on a
specific CLI name we launch the module directly with the same Python
interpreter, which works for every recent MemPalace version.
"""

import os
import shutil
import sys

from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.tools import McpStdioServerParameters
from trpc_agent_sdk.tools import StdioConnectionParams


# A small, curated default. Set to `None` to expose every tool the MemPalace
# server advertises. Trim the list to reduce model token usage and to keep the
# demo focused on a few representative tools.
_DEFAULT_TOOL_FILTER = [
    "mempalace_status",
    "mempalace_list_wings",
    "mempalace_search",
    "mempalace_add_drawer",
    "mempalace_kg_add",
    "mempalace_kg_query",
    "mempalace_kg_timeline",
    "mempalace_diary_write",
    "mempalace_diary_read",
]


def _resolve_server_command(palace_path: str | None) -> tuple[str, list[str]]:
    """Pick the best available command to launch the MemPalace MCP server.

    Priority:
      1. `python -m mempalace.mcp_server [--palace PATH]` (always works if the
         `mempalace` package is importable from the current interpreter).
      2. `mempalace-mcp` CLI shim (fallback if it is on PATH).
    """
    extra_args = ["--palace", palace_path] if palace_path else []

    # Sanity check that the `mempalace` package is importable from the current
    # interpreter. If not, we still try the CLI shim but warn early.
    try:
        import importlib.util  # noqa: WPS433
        if importlib.util.find_spec("mempalace") is not None:
            return sys.executable, ["-m", "mempalace.mcp_server", *extra_args]
    except Exception:  # pragma: no cover - defensive
        pass

    if shutil.which("mempalace-mcp"):
        return "mempalace-mcp", extra_args

    raise RuntimeError("Cannot find the MemPalace MCP server. Install MemPalace into the same Python environment "
                       "(`pip install -e \".[mempalace]\"`) so that `python -m mempalace.mcp_server` works.")


class MempalaceMCPToolset(MCPToolset):
    """Stdio-based MCP toolset bound to the MemPalace MCP server."""

    def __init__(self, palace_path: str | None = None, tool_filter: list[str] | None = _DEFAULT_TOOL_FILTER) -> None:
        super().__init__()

        env = os.environ.copy()
        if palace_path:
            env["MEMPALACE_PALACE_PATH"] = palace_path

        command, args = _resolve_server_command(palace_path)
        stdio_server_params = McpStdioServerParameters(
            command=command,
            args=args,
            env=env,
        )
        self._connection_params = StdioConnectionParams(
            server_params=stdio_server_params,
            timeout=30,    # palace warm-up + embedding model load can take a few seconds
        )
        if tool_filter is not None:
            self._tool_filter = tool_filter
