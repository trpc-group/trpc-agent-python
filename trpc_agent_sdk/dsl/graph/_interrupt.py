# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Graph-level interrupt API.

This wraps LangGraph interrupt so graph users can stay within trpc_agent_dsl APIs.
"""

from typing import TypeVar
from typing import cast

from langgraph.types import interrupt as _langgraph_interrupt

T = TypeVar("T")


def interrupt(value: T) -> T:
    """Interrupt graph execution and return resume payload on continuation.

    Args:
        value: Payload exposed to the client while execution is interrupted.

    Returns:
        Resume payload when graph execution continues.
    """
    return cast(T, _langgraph_interrupt(value))
