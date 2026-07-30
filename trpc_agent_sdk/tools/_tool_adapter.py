# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool adapter for TRPC Agent framework."""

from typing import Callable
from typing import Optional
from typing import TypeAlias
from typing import Union

from trpc_agent_sdk.abc import ToolSetABC as BaseToolSet
from trpc_agent_sdk.context import InvocationContext

from ._base_tool import BaseTool
from ._default_toolset import DefaultToolSet
from ._function_tool import FunctionTool
from ._registry import ToolRegistry
from ._registry import ToolSetRegistry
from ._registry import get_tool
from ._registry import get_tool_set

ToolTypeUnion: TypeAlias = Union[Callable, BaseTool, str]
ToolSetTypeUnion: TypeAlias = Union[Callable, BaseTool, BaseToolSet, str]

# inner type
ToolUnion: TypeAlias = Union[BaseTool, BaseToolSet]


def create_tool(tool: Union[list[ToolTypeUnion], ToolTypeUnion],
                need_cache: bool = False,
                filters_name: Optional[list[str]] = None) -> Union[BaseTool, list[BaseTool]]:
    """Create a tool."""
    tools = tool
    is_list = isinstance(tool, list)
    if not is_list:
        tools = [tool]
    new_tools = []
    filters_name = filters_name or []
    for tool in tools:
        new_tool = None
        if isinstance(tool, Callable):
            new_tool = FunctionTool(func=tool)
            new_tool.add_filters(filters_name, True)
        elif isinstance(tool, BaseTool):
            new_tool = tool
            new_tool.add_filters(filters_name, False)
        elif isinstance(tool, str):
            new_tool = get_tool(tool)
            if not new_tool:
                raise ValueError(f"Cannot find tool {tool}")
            new_tool.add_filters(filters_name, False)
        else:
            raise TypeError(f"Unsupported tool type: {type(tool)}")
        if need_cache:
            ToolRegistry().add(new_tool)
        new_tools.append(new_tool)
    if is_list:
        return new_tools
    return new_tools[0]


def create_toolset(tool_set: Union[list[ToolSetTypeUnion], ToolSetTypeUnion],
                   need_cache: bool = False) -> Union[BaseToolSet, list[BaseToolSet]]:
    """Convert a tool to a toolset."""
    tool_sets = tool_set
    is_list = isinstance(tool_sets, list)
    if not is_list:
        tool_sets = [tool_sets]
    new_toolsets: list[BaseToolSet] = []
    default_toolset = None
    for toolset in tool_sets:
        new_toolset = None
        if isinstance(toolset, BaseToolSet):
            new_toolset = toolset
        elif isinstance(toolset, str):
            toolset = get_tool_set(name=toolset)
            if not toolset:
                raise ValueError(f"Cannot find toolset {toolset}")
            new_toolset = toolset
        else:
            tool = create_tool(toolset)
            if default_toolset is None:
                default_toolset = DefaultToolSet()
            default_toolset.add_tools([tool])
        if new_toolset:
            if need_cache:
                ToolSetRegistry().add(new_toolset)
            new_toolsets.append(new_toolset)
    if default_toolset is not None:
        new_toolsets.append(default_toolset)
    if is_list:
        return new_toolsets
    return new_toolsets[0]


async def convert_toolunion_to_tool_list(toolsets: list[ToolUnion],
                                         invocation_context: InvocationContext) -> list[BaseTool]:
    """Convert a tool union to a list of tools."""
    new_tools = []
    for toolset in toolsets:
        if isinstance(toolset, BaseToolSet):
            new_tools.extend(await toolset.get_tools(invocation_context))
        elif isinstance(toolset, BaseTool):
            new_tools.append(toolset)
        else:
            raise TypeError(f"Unsupported tool type: {type(toolset)}")
    return new_tools
