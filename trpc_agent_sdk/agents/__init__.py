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
from ._langgraph_agent import LangGraphAgent
from ._llm_agent import LlmAgent
from ._parallel_agent import ParallelAgent
from ._transfer_agent import TransferAgent
from .core import BranchFilterMode
from .core import TimelineFilterMode
from .utils import get_agent_context
from .utils import get_agent_context as get_langgraph_agent_context
from .utils import get_langgraph_payload
from .utils import langgraph_llm_node
from .utils import langgraph_tool_node

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
