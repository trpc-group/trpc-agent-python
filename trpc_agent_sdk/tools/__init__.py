# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tools module for TRPC Agent framework."""

from typing import TYPE_CHECKING

from trpc_agent_sdk.abc import ToolPredicate
from trpc_agent_sdk.abc import ToolSetABC as BaseToolSet

if TYPE_CHECKING:
    # Lazy re-export — see ``_LAZY_REEXPORTS`` below.
    from trpc_agent_sdk.agents.sub_agent import DynamicSubAgentTool as DynamicSubAgentTool  # noqa: F401
    from trpc_agent_sdk.agents.sub_agent import SpawnSubAgentTool as SpawnSubAgentTool  # noqa: F401

from ._agent_tool import AGENT_TOOL_APP_NAME_SUFFIX, AgentTool
from ._base_tool import BaseTool
from ._constants import TOOL_NAME
from ._context_var import get_tool_var, reset_tool_var, set_tool_var
from ._default_toolset import DefaultToolSet
from ._function_tool import FunctionTool
from ._load_memory_tool import LoadMemoryResponse, LoadMemoryTool, load_memory, load_memory_tool
from ._long_running_tool import LongRunningFunctionTool, is_tool_execution_error
from ._preload_memory_tool import PreloadMemoryTool, preload_memory_tool
from ._registry import ToolRegistry, ToolSetRegistry, ToolType, get_tool, get_tool_set, register_tool, register_tool_set
from ._set_model_response_tool import SetModelResponseTool
from ._streaming_function_tool import StreamingFunctionTool
from ._streaming_progress_tool import StreamingProgressTool
from ._todo_tool import (
    DEFAULT_NUDGE_MESSAGE,
    DEFAULT_STATE_KEY_PREFIX,
    DEFAULT_TODO_DESCRIPTION,
    DEFAULT_TODO_PROMPT,
    TodoItem,
    TodoStatus,
    TodoWriteTool,
    get_todos,
    render_todos,
    state_key,
    validate_todos,
)
from ._tool_adapter import convert_toolunion_to_tool_list, create_tool, create_toolset
from ._transfer_to_agent_tool import transfer_to_agent
from ._webfetch_tool import FetchResult, WebFetchTool
from ._websearch_tool import SearchHit, WebSearchResult, WebSearchTool
from .file_tools import BashTool, EditTool, FileToolSet, GlobTool, GrepTool, ReadTool, WriteTool
from .goal_tools import (
    GoalOptions,
    GoalRecord,
    GoalStatus,
    GoalToolSet,
    OnRetry,
    RetryEvent,
    get_goal_record,
    render_goal,
    setup_goal,
)
from .mcp_tool import (
    McpConnectionParamsType,
    McpStdioServerParameters,
    MCPTool,
    MCPToolset,
    SseConnectionParams,
    StdioConnectionParams,
    StreamableHTTPConnectionParams,
    patch_mcp_cancel_scope_exit_issue,
)
from .task_tools import (
    DEFAULT_TASK_PROMPT,
    TaskCreateTool,
    TaskGetTool,
    TaskListSummary,
    TaskListTool,
    TaskRecord,
    TaskStatus,
    TaskStore,
    TaskToolSet,
    TaskUpdateTool,
    get_task_store,
    render_task_list,
)
from .utils import (
    build_function_declaration,
    from_function_with_options,
    get_required_fields,
    parse_schema_from_parameter,
    register_checker,
)

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
    "is_tool_execution_error",
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
    "StreamingProgressTool",
    "convert_toolunion_to_tool_list",
    "create_tool",
    "create_toolset",
    "transfer_to_agent",
    "TodoWriteTool",
    "TodoItem",
    "TodoStatus",
    "get_todos",
    "state_key",
    "render_todos",
    "validate_todos",
    "DEFAULT_TODO_PROMPT",
    "DEFAULT_TODO_DESCRIPTION",
    "DEFAULT_NUDGE_MESSAGE",
    "DEFAULT_STATE_KEY_PREFIX",
    "TaskCreateTool",
    "TaskUpdateTool",
    "TaskGetTool",
    "TaskListTool",
    "TaskToolSet",
    "TaskStatus",
    "TaskRecord",
    "TaskStore",
    "TaskListSummary",
    "get_task_store",
    "render_task_list",
    "DEFAULT_TASK_PROMPT",
    "GoalStatus",
    "GoalRecord",
    "GoalToolSet",
    "GoalOptions",
    "RetryEvent",
    "OnRetry",
    "setup_goal",
    "get_goal_record",
    "render_goal",
    "FetchResult",
    "WebFetchTool",
    "SearchHit",
    "WebSearchResult",
    "WebSearchTool",
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

# Lazy re-exports: implemented elsewhere (avoids circular imports and keeps
# the tools package free of optional file/web tool dependencies) but exposed
# here for discoverability. Not in ``__all__`` so ``import *`` stays lazy.
_LAZY_REEXPORTS = {
    "DynamicSubAgentTool": ("trpc_agent_sdk.agents.sub_agent", "DynamicSubAgentTool"),
    "SpawnSubAgentTool": ("trpc_agent_sdk.agents.sub_agent", "SpawnSubAgentTool"),
}


def __getattr__(name):
    if name in _LAZY_REEXPORTS:
        import importlib

        module_name, attr = _LAZY_REEXPORTS[name]
        obj = getattr(importlib.import_module(module_name), attr)
        globals()[name] = obj  # cache: subsequent accesses skip __getattr__
        return obj
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(list(globals()) + list(_LAZY_REEXPORTS)))
