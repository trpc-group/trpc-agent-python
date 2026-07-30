# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Base class for protocol event translators."""

from abc import ABC
from abc import abstractmethod
from typing import AsyncGenerator
from typing import Generic
from typing import TypeVar

from ._event import Event as TrpcEvent

# Type variables for protocol-specific events and context
ProtocolEventT = TypeVar("ProtocolEventT")
ContextT = TypeVar("ContextT")


class EventTranslatorBase(ABC, Generic[ProtocolEventT, ContextT]):
    """Base class for protocol event translators that convert trpc Events.

    This abstract base class defines the interface for translating trpc Events
    to protocol-specific events (e.g., AG-UI BaseEvent, A2A Event).

    Users implement:
    - need_translate(): Filter which events to handle
    - translate(): Convert events to protocol-specific format

    The context parameter provides metadata needed for building events
    (e.g., task_id, context_id for A2A; thread_id, run_id for AG-UI).

    Type Parameters:
        ProtocolEventT: The type of protocol-specific event this translator produces
        ContextT: The type of context object passed to translate()
    """

    @abstractmethod
    def need_translate(self, event: TrpcEvent) -> bool:
        """Check if this event should be translated by this translator.

        Subclasses must implement this method to determine which events
        they should handle.

        Args:
            event: The trpc Event to check

        Returns:
            True if this translator should translate this event
        """

    @abstractmethod
    async def translate(
        self,
        event: TrpcEvent,
        context: ContextT,
    ) -> AsyncGenerator[ProtocolEventT, None]:
        """Translate trpc Event to protocol events.

        Args:
            event: The trpc Event to translate
            context: Protocol-specific context (e.g., AgUiTranslationContext, A2aTranslationContext)

        Yields:
            Protocol-specific events
        """
