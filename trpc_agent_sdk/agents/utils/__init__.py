# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
TRPC Agent Context Utilities Module.
"""
from typing import Any

from ._langgraph_event_writer import LANGGRAPH_EVENT_TYPE
from ._langgraph_event_writer import LangGraphEventType
from ._langgraph_event_writer import LangGraphEventWriter
from ._langgraph_event_writer import TRPC_EVENT_MARKER
from ._langgraph_event_writer import extract_trpc_event
from ._langgraph_event_writer import get_event_type
from ._langgraph_event_writer import is_trpc_event_chunk

_LANGGRAPH_EXPORTS = {
    "AGENT_CTX_KEY",
    "CHUNK_KEY",
    "LANGGRAPH_KEY",
    "STREAM_MODE_KEY",
    "TRPC_AGENT_KEY",
    "get_agent_context",
    "get_langgraph_payload",
    "langgraph_llm_node",
    "langgraph_tool_node",
}


def __getattr__(name: str) -> Any:
    """Load LangGraph context helpers only when explicitly requested."""
    if name not in _LANGGRAPH_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    try:
        from . import _langgraph
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing == "langgraph" or missing.startswith(
                "langgraph.") or missing == "langchain_core" or missing.startswith("langchain_core."):
            raise ImportError(f"{name} requires the optional 'graph' dependencies. "
                              'Install them with: pip install "trpc-agent-py[graph]"') from exc
        raise

    value = getattr(_langgraph, name)
    globals()[name] = value
    return value


__all__ = [
    "AGENT_CTX_KEY",
    "CHUNK_KEY",
    "LANGGRAPH_KEY",
    "STREAM_MODE_KEY",
    "TRPC_AGENT_KEY",
    "get_agent_context",
    "get_langgraph_payload",
    "langgraph_llm_node",
    "langgraph_tool_node",
    "LANGGRAPH_EVENT_TYPE",
    "LangGraphEventType",
    "LangGraphEventWriter",
    "TRPC_EVENT_MARKER",
    "extract_trpc_event",
    "get_event_type",
    "is_trpc_event_chunk",
]
