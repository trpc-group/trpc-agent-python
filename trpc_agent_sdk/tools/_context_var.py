# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Set context variables for tool"""

import contextvars

from trpc_agent_sdk.abc import ToolABC

_tool: contextvars.ContextVar[ToolABC] = contextvars.ContextVar("_tool", default=None)  # type: ignore


def set_tool_var(tool: ToolABC) -> contextvars.Token[ToolABC]:
    """Set tool variable"""
    return _tool.set(tool)


def get_tool_var() -> ToolABC:
    """Get tool variable"""
    return _tool.get()


def reset_tool_var(token: contextvars.Token[ToolABC]) -> None:
    """Reset tool variable"""
    try:
        _tool.reset(token)
    except ValueError:
        return None
