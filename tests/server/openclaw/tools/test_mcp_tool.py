"""Unit tests for trpc_agent_sdk.server.openclaw.tools.mcp_tool module."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from trpc_agent_sdk.server.openclaw.tools.mcp_tool import build_mcp_toolsets


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# The source code iterates `mcp_servers.values()` and unpacks each value as
# `(name, cfg)`.  To exercise code paths we supply dicts whose values are
# `(name, config)` 2-tuples.


def _servers(**entries):
    """Build mcp_servers dict whose values are (name, cfg) tuples."""
    return {k: (k, v) for k, v in entries.items()}


# ---------------------------------------------------------------------------
# build_mcp_toolsets — empty / None
# ---------------------------------------------------------------------------


class TestBuildMcpToolsetsEmpty:

    def test_none_returns_empty(self):
        assert build_mcp_toolsets(None) == []

    def test_empty_dict_returns_empty(self):
        assert build_mcp_toolsets({}) == []


# ---------------------------------------------------------------------------
# build_mcp_toolsets — stdio type
# ---------------------------------------------------------------------------


class TestBuildMcpToolsetsStdio:

    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.MCPToolset")
    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.StdioConnectionParams")
    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.McpStdioServerParameters")
    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.patch_mcp_cancel_scope_exit_issue")
    def test_stdio_type(self, mock_patch, mock_params, mock_conn, mock_toolset):
        servers = _servers(my_stdio={"type": "stdio", "command": "node", "args": ["server.js"], "env": {"KEY": "VAL"}})
        result = build_mcp_toolsets(servers)
        mock_patch.assert_called_once()
        mock_params.assert_called_once_with(command="node", args=["server.js"], env={"KEY": "VAL"})
        mock_conn.assert_called_once()
        assert len(result) == 1

    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.MCPToolset")
    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.StdioConnectionParams")
    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.McpStdioServerParameters")
    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.patch_mcp_cancel_scope_exit_issue")
    def test_stdio_inferred_from_command(self, mock_patch, mock_params, mock_conn, mock_toolset):
        """When type is omitted but 'command' is present, stdio is inferred."""
        servers = _servers(cmd={"command": "python3", "args": ["-m", "server"]})
        result = build_mcp_toolsets(servers)
        mock_params.assert_called_once()
        assert len(result) == 1

    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.patch_mcp_cancel_scope_exit_issue")
    def test_stdio_missing_command_skipped(self, mock_patch):
        servers = _servers(bad={"type": "stdio", "command": ""})
        result = build_mcp_toolsets(servers)
        assert result == []

    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.MCPToolset")
    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.StdioConnectionParams")
    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.McpStdioServerParameters")
    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.patch_mcp_cancel_scope_exit_issue")
    def test_stdio_custom_timeout(self, mock_patch, mock_params, mock_conn, mock_toolset):
        servers = _servers(s={"type": "stdio", "command": "node", "tool_timeout": 120})
        build_mcp_toolsets(servers)
        conn_call = mock_conn.call_args
        assert conn_call.kwargs.get("timeout") == 120.0 or conn_call[1].get("timeout") == 120.0


# ---------------------------------------------------------------------------
# build_mcp_toolsets — sse type
# ---------------------------------------------------------------------------


class TestBuildMcpToolsetsSse:

    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.MCPToolset")
    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.SseConnectionParams")
    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.patch_mcp_cancel_scope_exit_issue")
    def test_sse_type(self, mock_patch, mock_conn, mock_toolset):
        servers = _servers(sse_srv={"type": "sse", "url": "https://sse.example.com", "headers": {"X-Key": "val"}})
        result = build_mcp_toolsets(servers)
        mock_conn.assert_called_once_with(
            url="https://sse.example.com",
            headers={"X-Key": "val"},
            timeout=30.0,
        )
        assert len(result) == 1

    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.patch_mcp_cancel_scope_exit_issue")
    def test_sse_missing_url_skipped(self, mock_patch):
        servers = _servers(bad={"type": "sse", "url": ""})
        result = build_mcp_toolsets(servers)
        assert result == []


# ---------------------------------------------------------------------------
# build_mcp_toolsets — streamablehttp type
# ---------------------------------------------------------------------------


class TestBuildMcpToolsetsStreamableHttp:

    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.MCPToolset")
    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.StreamableHTTPConnectionParams")
    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.patch_mcp_cancel_scope_exit_issue")
    def test_streamablehttp_type(self, mock_patch, mock_conn, mock_toolset):
        servers = _servers(http_srv={"type": "streamableHttp", "url": "https://http.example.com"})
        result = build_mcp_toolsets(servers)
        mock_conn.assert_called_once()
        assert len(result) == 1

    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.patch_mcp_cancel_scope_exit_issue")
    def test_streamablehttp_missing_url_skipped(self, mock_patch):
        servers = _servers(bad={"type": "streamableHttp", "url": ""})
        result = build_mcp_toolsets(servers)
        assert result == []


# ---------------------------------------------------------------------------
# build_mcp_toolsets — unknown type
# ---------------------------------------------------------------------------


class TestBuildMcpToolsetsUnknown:

    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.patch_mcp_cancel_scope_exit_issue")
    def test_unknown_type_skipped(self, mock_patch):
        servers = _servers(weird={"type": "grpc", "url": "localhost:50051"})
        result = build_mcp_toolsets(servers)
        assert result == []


# ---------------------------------------------------------------------------
# build_mcp_toolsets — None config value
# ---------------------------------------------------------------------------


class TestBuildMcpToolsetsNoneConfig:

    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.patch_mcp_cancel_scope_exit_issue")
    def test_none_config_skipped(self, mock_patch):
        servers = {"key": ("my_server", None)}
        result = build_mcp_toolsets(servers)
        assert result == []


# ---------------------------------------------------------------------------
# build_mcp_toolsets — pydantic-like objects
# ---------------------------------------------------------------------------


class TestBuildMcpToolsetsPydanticConfig:

    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.MCPToolset")
    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.StdioConnectionParams")
    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.McpStdioServerParameters")
    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.patch_mcp_cancel_scope_exit_issue")
    def test_pydantic_model_dump(self, mock_patch, mock_params, mock_conn, mock_toolset):
        cfg_obj = MagicMock()
        cfg_obj.model_dump.return_value = {
            "type": "stdio",
            "command": "python3",
            "args": ["-m", "myserver"],
            "env": {},
            "tool_timeout": 60,
        }
        servers = {"key": ("pydantic_server", cfg_obj)}
        result = build_mcp_toolsets(servers)
        cfg_obj.model_dump.assert_called_once()
        assert len(result) == 1


# ---------------------------------------------------------------------------
# build_mcp_toolsets — multiple servers
# ---------------------------------------------------------------------------


class TestBuildMcpToolsetsMultiple:

    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.MCPToolset")
    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.StreamableHTTPConnectionParams")
    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.SseConnectionParams")
    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.StdioConnectionParams")
    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.McpStdioServerParameters")
    @patch("trpc_agent_sdk.server.openclaw.tools.mcp_tool.patch_mcp_cancel_scope_exit_issue")
    def test_multiple_servers(self, mock_patch, mock_params, mock_stdio, mock_sse, mock_http, mock_toolset):
        servers = {
            "k1": ("s1", {"type": "stdio", "command": "node"}),
            "k2": ("s2", {"type": "sse", "url": "https://sse.example.com"}),
            "k3": ("s3", {"type": "streamableHttp", "url": "https://http.example.com"}),
            "k4": ("s4", {"type": "unknown"}),
        }
        result = build_mcp_toolsets(servers)
        assert len(result) == 3
