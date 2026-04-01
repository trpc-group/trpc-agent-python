# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tools module for TRPC Agent framework."""

from trpc_agent_sdk.abc import ToolPredicate
from trpc_agent_sdk.abc import ToolSetABC as BaseToolSet

from ._agent_tool import AGENT_TOOL_APP_NAME_SUFFIX
from ._agent_tool import AgentTool
from ._base_tool import BaseTool
from ._constants import TOOL_NAME
from ._context_var import get_tool_var
from ._context_var import reset_tool_var
from ._context_var import set_tool_var
from ._default_toolset import DefaultToolSet
from ._function_tool import FunctionTool
from ._load_memory_tool import LoadMemoryResponse
from ._load_memory_tool import LoadMemoryTool
from ._load_memory_tool import load_memory
from ._load_memory_tool import load_memory_tool
from ._long_running_tool import LongRunningFunctionTool
from ._preload_memory_tool import PreloadMemoryTool
from ._preload_memory_tool import preload_memory_tool
from ._registry import ToolRegistry
from ._registry import ToolSetRegistry
from ._registry import ToolType
from ._registry import get_tool
from ._registry import get_tool_set
from ._registry import register_tool
from ._registry import register_tool_set
from ._set_model_response_tool import SetModelResponseTool
from ._streaming_function_tool import StreamingFunctionTool
from ._tool_adapter import convert_toolunion_to_tool_list
from ._tool_adapter import create_tool
from ._tool_adapter import create_toolset
from ._transfer_to_agent_tool import transfer_to_agent
from .file_tools import BashTool
from .file_tools import EditTool
from .file_tools import FileToolSet
from .file_tools import GlobTool
from .file_tools import GrepTool
from .file_tools import ReadTool
from .file_tools import WriteTool
from .mcp_tool import MCPTool
from .mcp_tool import MCPToolset
from .mcp_tool import McpConnectionParamsType
from .mcp_tool import McpStdioServerParameters
from .mcp_tool import SseConnectionParams
from .mcp_tool import StdioConnectionParams
from .mcp_tool import StreamableHTTPConnectionParams
from .mcp_tool import patch_mcp_cancel_scope_exit_issue
from .utils import build_function_declaration
from .utils import from_function_with_options
from .utils import get_required_fields
from .utils import parse_schema_from_parameter
from .utils import register_checker

__all__ = [
    "ToolPredicate",
    "BaseToolSet",
    "AGENT_TOOL_APP_NAME_SUFFIX",
    "AgentTool",
    "BaseTool",
    "get_tool_var",
    "reset_tool_var",
    "set_tool_var",
    "DefaultToolSet",
    "FunctionTool",
    "LoadMemoryResponse",
    "LoadMemoryTool",
    "load_memory",
    "load_memory_tool",
    "LongRunningFunctionTool",
    "PreloadMemoryTool",
    "preload_memory_tool",
    "ToolRegistry",
    "ToolSetRegistry",
    "ToolType",
    "TOOL_NAME",
    "get_tool",
    "get_tool_set",
    "register_tool",
    "register_tool_set",
    "SetModelResponseTool",
    "StreamingFunctionTool",
    "convert_toolunion_to_tool_list",
    "create_tool",
    "create_toolset",
    "transfer_to_agent",
    "BashTool",
    "EditTool",
    "FileToolSet",
    "GlobTool",
    "GrepTool",
    "ReadTool",
    "WriteTool",
    "MCPTool",
    "MCPToolset",
    "McpConnectionParamsType",
    "McpStdioServerParameters",
    "SseConnectionParams",
    "StdioConnectionParams",
    "StreamableHTTPConnectionParams",
    "patch_mcp_cancel_scope_exit_issue",
    "build_function_declaration",
    "from_function_with_options",
    "get_required_fields",
    "parse_schema_from_parameter",
    "register_checker",
]
