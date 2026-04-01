# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
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
