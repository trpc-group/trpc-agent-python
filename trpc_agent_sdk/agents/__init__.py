# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent system core components module.

This module exports the fundamental classes and types required for building
and working with TRPC agents. It provides the base agent class, callback filter,
multi-agent composition patterns, and all essential type definitions for agent development.
"""
from typing import Any

from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import new_invocation_context_id

from ._base_agent import BaseAgent
from ._base_agent import InstructionProvider
from ._callback import AgentCallback
from ._callback import AgentCallbackFilter
from ._callback import CallbackFilter
from ._callback import ModelCallback
from ._callback import ModelCallbackFilter
from ._callback import ToolCallback
from ._callback import ToolCallbackFilter
from ._chain_agent import ChainAgent
from ._cycle_agent import CycleAgent
from ._llm_agent import LlmAgent
from ._parallel_agent import ParallelAgent
from ._transfer_agent import TransferAgent
from .core import BranchFilterMode
from .core import TimelineFilterMode

_GRAPH_EXPORTS = {
    "LangGraphAgent",
    "get_agent_context",
    "get_langgraph_agent_context",
    "get_langgraph_payload",
    "langgraph_llm_node",
    "langgraph_tool_node",
}


def __getattr__(name: str) -> Any:
    """Load LangGraph integrations only when their public API is requested."""
    if name not in _GRAPH_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    try:
        if name == "LangGraphAgent":
            from ._langgraph_agent import LangGraphAgent

            value = LangGraphAgent
        else:
            from . import utils

            source_name = "get_agent_context" if name == "get_langgraph_agent_context" else name
            value = getattr(utils, source_name)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing == "langgraph" or missing.startswith(
                "langgraph.") or missing == "langchain_core" or missing.startswith("langchain_core."):
            raise ImportError(f"{name} requires the optional 'graph' dependencies. "
                              'Install them with: pip install "trpc-agent-py[graph]"') from exc
        raise

    globals()[name] = value
    return value


__all__ = [
    "RunConfig",
    "InvocationContext",
    "new_invocation_context_id",
    "BaseAgent",
    "InstructionProvider",
    "AgentCallback",
    "AgentCallbackFilter",
    "ModelCallback",
    "ModelCallbackFilter",
    "ToolCallback",
    "ToolCallbackFilter",
    "ChainAgent",
    "CycleAgent",
    "LangGraphAgent",
    "LlmAgent",
    "TransferAgent",
    "BranchFilterMode",
    "TimelineFilterMode",
    "ParallelAgent",
    "get_agent_context",
    "get_langgraph_agent_context",
    "langgraph_llm_node",
    "langgraph_tool_node",
    "CallbackFilter",
    "get_langgraph_payload",
]

# Rebuild Pydantic models to resolve forward references after all imports are complete
InvocationContext.model_rebuild()
LlmAgent.model_rebuild()
ChainAgent.model_rebuild()
ParallelAgent.model_rebuild()
CycleAgent.model_rebuild()
TransferAgent.model_rebuild()
