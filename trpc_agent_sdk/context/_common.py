# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""TRPC Context Helper Functions.

This module provides core utilities for managing TRPC context objects,
including context getter/setter functions and specialized context handlers.

Functions:
    get_data_by_agent_ctx: Retrieve generic context from TRPC context
    set_data_to_agent_ctx: Store generic context in TRPC context
    set_invocation_ctx: Store invocation context
    get_invocation_ctx: Retrieve invocation context
    create_agent_context: Create new TRPC context instance
"""
import contextvars
from typing import Any
from typing import Optional

from ._agent_context import AgentContext
from ._constants import INVOCATION_CTX
from ._invocation_context import InvocationContext


def get_data_by_agent_ctx(agent_ctx: AgentContext, name: str, default: Any = None) -> Optional[Any]:
    """Get context from trpc agent context"""
    return agent_ctx.get_metadata(name, default)


def pop_data_by_agent_ctx(agent_ctx: AgentContext, name: str, default: Any = None) -> Optional[Any]:
    """Get context from trpc agent context"""
    return agent_ctx.metadata.pop(name, default)


def set_data_to_agent_ctx(agent_ctx: AgentContext, name: str, data: Any):
    """Set context to trpc agent context"""
    agent_ctx.with_metadata(name, data)


invocation_ctx: contextvars.ContextVar[InvocationContext] = contextvars.ContextVar(INVOCATION_CTX,
                                                                                   default=None)  # type: ignore


def set_invocation_ctx(ctx: InvocationContext) -> contextvars.Token:
    """Set invocation context to trpc invocation context"""
    return invocation_ctx.set(ctx)


def get_invocation_ctx() -> InvocationContext:
    """Get invocation context from trpc invocation context"""
    return invocation_ctx.get()


def reset_invocation_ctx(token: contextvars.Token) -> None:
    """Pop invocation context from trpc invocation context"""
    try:
        return invocation_ctx.reset(token)
    except ValueError:
        return None


def create_agent_context() -> AgentContext:
    """Create trpc agent context"""
    return AgentContext()
