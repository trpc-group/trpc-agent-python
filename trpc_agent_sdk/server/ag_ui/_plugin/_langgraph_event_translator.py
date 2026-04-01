# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""AG-UI event translator for LangGraph events (inheritable)."""

from abc import ABC
from dataclasses import dataclass

from trpc_agent_sdk.agents.utils import TRPC_EVENT_MARKER
from trpc_agent_sdk.events import Event as TrpcEvent
from trpc_agent_sdk.events import EventTranslatorBase


@dataclass
class AgUiTranslationContext:
    """Context for AG-UI event translation.

    Attributes:
        thread_id: The AG-UI thread ID
        run_id: The AG-UI run ID
    """
    thread_id: str
    run_id: str


class AgUiLangGraphEventTranslator(EventTranslatorBase, ABC):
    """Base translator for LangGraph trpc Events to AG-UI events.

    This class provides the need_translate() implementation to identify
    LangGraph events (those with TRPC_EVENT_MARKER in custom_metadata).

    Users must inherit from this class and implement the translate() method
    to define how to convert events to AG-UI protocol events.

    Example:
        class MyAgUiTranslator(AgUiLangGraphEventTranslator):
            async def translate(self, event, context):
                # Your translation logic here
                event_type = event.custom_metadata.get(LANGGRAPH_EVENT_TYPE)
                if event_type == "text":
                    # Handle text events
                    yield create_text_message_event(event, context)
                elif event_type == "custom":
                    # Handle custom events
                    custom_data = event.custom_metadata.get("data", {})
                    yield CustomEvent(
                        type=EventType.CUSTOM,
                        name="my_custom",
                        value=custom_data,
                        timestamp=int(event.timestamp * 1000),
                    )
    """

    def need_translate(self, event: TrpcEvent) -> bool:
        """Check if this event was created by LangGraphEventWriter.

        Args:
            event: The trpc Event to check

        Returns:
            True if this event has TRPC_EVENT_MARKER in custom_metadata
        """
        return (event.custom_metadata is not None and TRPC_EVENT_MARKER in event.custom_metadata)
