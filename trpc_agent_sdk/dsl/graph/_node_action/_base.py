# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Base class for node action executors.

This module provides the abstract base class for all node action types
(LLM, Tool, Agent). Each action type implements specific execution logic
while sharing common infrastructure.
"""

from abc import ABC
from abc import abstractmethod
from typing import Any
from typing import Optional

from trpc_agent_sdk.context import InvocationContext

from .._event_writer import AsyncEventWriter
from .._event_writer import EventWriter
from .._state import State


class BaseNodeAction(ABC):
    """Base class for node action executors.

    Node actions encapsulate the execution logic for different node types.
    Each action type (LLM, Tool, Agent) inherits from this class and
    implements the execute method.

    Attributes:
        name: Name of the node
        writer: EventWriter for high-frequency streaming events
        async_writer: AsyncEventWriter for lifecycle start/complete/error events
        ctx: Optional invocation context

    Example:
        >>> class CustomNodeAction(BaseNodeAction):
        ...     async def execute(self, state: State) -> dict[str, Any]:
        ...         self.writer.write_text("Processing...")
        ...         return {"result": "done"}
    """

    def __init__(
        self,
        name: str,
        writer: EventWriter,
        async_writer: AsyncEventWriter,
        ctx: Optional[InvocationContext] = None,
    ):
        """Initialize the node action.

        Args:
            name: Name of the node
            writer: EventWriter for high-frequency streaming events
            async_writer: AsyncEventWriter for lifecycle events
            ctx: Optional invocation context
        """
        self.name = name
        self.writer = writer
        self.async_writer = async_writer
        self.ctx = ctx

    @abstractmethod
    async def execute(self, state: State) -> dict[str, Any]:
        """Execute the node action and return state update.

        Args:
            state: Current state

        Returns:
            State update dictionary
        """
        pass
