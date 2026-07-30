# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
TRPC Agent Context Module Initialization.

This module serves as the entry point for the TRPC Agent context system,
providing the core context-related classes and utilities. Key exports include:

1. Core Components:
   - InvocationContext: Base context class for agent operations
   - new_invocation_context_id: Context ID generator

2. Purpose:
   - Centralizes context-related imports
   - Provides clean namespace for context operations
   - Enables consistent context handling across the system

3. Usage Patterns:
   - Direct import of context classes
   - ID generation for new contexts
   - Tool execution context management
"""

from ._agent_context import AgentContext
from ._agent_context import new_agent_context
from ._common import create_agent_context
from ._common import get_data_by_agent_ctx
from ._common import get_invocation_ctx
from ._common import pop_data_by_agent_ctx
from ._common import reset_invocation_ctx
from ._common import set_data_to_agent_ctx
from ._common import set_invocation_ctx
from ._invocation_context import InvocationContext
from ._invocation_context import new_invocation_context_id

__all__ = [
    "AgentContext",
    "new_agent_context",
    "create_agent_context",
    "get_data_by_agent_ctx",
    "get_invocation_ctx",
    "pop_data_by_agent_ctx",
    "reset_invocation_ctx",
    "set_data_to_agent_ctx",
    "set_invocation_ctx",
    "InvocationContext",
    "new_invocation_context_id",
]
