# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Filter package initialization module.

This module exports the core filter interfaces and management utilities.
"""

from trpc_agent_sdk.abc import FilterAsyncGenHandleType
from trpc_agent_sdk.abc import FilterAsyncGenReturnType
from trpc_agent_sdk.abc import FilterHandleType
from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.abc import FilterReturnType
from trpc_agent_sdk.abc import FilterType

from ._base_filter import BaseFilter
from ._filter_runner import FilterRunner
from ._registry import FilterRegistry
from ._registry import get_agent_filter
from ._registry import get_filter
from ._registry import get_model_filter
from ._registry import get_tool_filter
from ._registry import register_agent_filter
from ._registry import register_filter
from ._registry import register_model_filter
from ._registry import register_tool_filter
from ._run_filter import AgentFilterAsyncGenHandleType
from ._run_filter import coroutine_handler_adapter
from ._run_filter import run_filters
from ._run_filter import run_stream_filters
from ._run_filter import stream_handler_adapter

__all__ = [
    "FilterAsyncGenHandleType",
    "FilterAsyncGenReturnType",
    "FilterHandleType",
    "FilterResult",
    "FilterReturnType",
    "FilterType",
    "BaseFilter",
    "FilterRunner",
    "FilterRegistry",
    "get_agent_filter",
    "get_filter",
    "get_model_filter",
    "get_tool_filter",
    "register_agent_filter",
    "register_filter",
    "register_model_filter",
    "register_tool_filter",
    "AgentFilterAsyncGenHandleType",
    "coroutine_handler_adapter",
    "run_filters",
    "run_stream_filters",
    "stream_handler_adapter",
]
