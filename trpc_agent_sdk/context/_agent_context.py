# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent context for TRPC Agent framework."""

from typing import Any
from typing import Dict

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import PrivateAttr


class AgentContext(BaseModel):
    """
    AgentContext is user context for trpc_agent_sdk.
    Used to control the interaction between user and framework.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
    )

    trpc_ctx: Any = None
    """Trpc context."""

    # Private attributes that cannot be set directly
    _timeout: int = PrivateAttr(default=3000)
    """Timeout in milliseconds."""

    _metadata: Dict[str, Any] = PrivateAttr(default_factory=dict)
    """Any metadata"""

    @property
    def timeout(self) -> int:
        """Get the timeout value."""
        return self._timeout

    def set_timeout(self, timeout: int) -> None:
        """Set the timeout value."""
        self._timeout = timeout

    @property
    def metadata(self) -> Dict[str, Any]:
        """Get the metadata dictionary."""
        return self._metadata

    def with_metadata(self, key: str, value: Any) -> None:
        """Add metadata with the given key and value."""
        self._metadata[key] = value

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Get metadata value by key with optional default."""
        return self._metadata.get(key, default)


def new_agent_context(timeout: int = 3000, metadata: Dict[str, Any] | None = None) -> AgentContext:
    """Create a new AgentContext instance."""
    context = AgentContext()
    context.set_timeout(timeout)

    if metadata is None:
        metadata = {}

    for key, value in metadata.items():
        context.with_metadata(key, value)

    return context
