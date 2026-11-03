# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Public types for deferred tool search."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable
from typing import Optional
from typing import Sequence

from trpc_agent_sdk.abc import ToolSetABC as BaseToolSet
from trpc_agent_sdk.context import InvocationContext

from .._base_tool import BaseTool


class InvocationMode(str, Enum):
    """How deferred tools are invoked after discovery."""

    NATIVE = "native"
    DISPATCH = "dispatch"


@dataclass(frozen=True)
class Toolbox:
    """A named group of static deferred tools."""

    name: str
    description: str
    tools: Sequence[BaseTool]


@dataclass(frozen=True)
class MCPToolbox:
    """A dynamically listed toolset exposed under an MCP-style namespace."""

    server_name: str
    description: str
    toolset: BaseToolSet


@dataclass(frozen=True)
class ToolSearchDocument:
    """Searchable representation of one deferred tool."""

    name: str
    description: str
    namespace: str
    text: str
    fingerprint: str


@dataclass
class ToolSearchEmbeddingUsage:
    """Embedding token usage accumulated by Tool Search."""

    prompt_tokens: int = 0
    total_tokens: int = 0

    def add(self, other: "ToolSearchEmbeddingUsage") -> None:
        """Merge another usage value into this accumulator."""
        self.prompt_tokens += other.prompt_tokens
        self.total_tokens += other.total_tokens

    def copy(self) -> "ToolSearchEmbeddingUsage":
        """Return a detached usage snapshot."""
        return ToolSearchEmbeddingUsage(
            prompt_tokens=self.prompt_tokens,
            total_tokens=self.total_tokens,
        )


ToolPermissionFilter = Callable[[InvocationContext, Sequence[str]], dict[str, bool]]
"""Return per-tool permissions for the active invocation."""


@dataclass(frozen=True)
class ToolSearchOptions:
    """Configuration for :class:`ToolSearchToolSet`."""

    max_results: int = 5
    permission_filter: Optional[ToolPermissionFilter] = None
    catalog_in_description: bool = False
    invocation_mode: InvocationMode = InvocationMode.NATIVE
    embedding_fail_open: bool = False
    state_key_prefix: str = "tool_search"
    strict_validation: bool = True
