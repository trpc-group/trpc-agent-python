# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Event utility functions for working with graph events.

This module provides helper functions for extracting information from
Event objects, as well as an EventUtils class that groups these as
static/class methods.
"""

from typing import Optional
from typing import Type
from typing import TypeVar

from trpc_agent_sdk.events import Event

from ._constants import METADATA_KEY_MODEL
from ._constants import METADATA_KEY_NODE
from ._constants import METADATA_KEY_TOOL
from ._metadata import ModelExecutionMetadata
from ._metadata import NodeExecutionMetadata
from ._metadata import ToolExecutionMetadata

T = TypeVar('T')


class EventUtils:
    """Utility methods for working with graph events."""

    @staticmethod
    def is_graph_event(event: Event) -> bool:
        """Check if an event is a graph execution event by object type."""
        return bool(event.object and event.object.startswith("graph."))

    @staticmethod
    def get_event_type(event: Event) -> Optional[str]:
        """Get the graph event type from Event.object."""
        return event.object

    @staticmethod
    def get_node_id(event: Event) -> Optional[str]:
        """Get the node ID from a graph event.

        Extracts from structured metadata if available, otherwise from flat state_delta.
        """
        if not event.actions or not event.actions.state_delta:
            return None

        state_delta = event.actions.state_delta

        # Try structured metadata first
        if METADATA_KEY_NODE in state_delta:
            node_metadata = state_delta[METADATA_KEY_NODE]
            if isinstance(node_metadata, dict):
                return node_metadata.get("node_id")

        if METADATA_KEY_TOOL in state_delta:
            tool_metadata = state_delta[METADATA_KEY_TOOL]
            if isinstance(tool_metadata, dict):
                return tool_metadata.get("node_id")

        if METADATA_KEY_MODEL in state_delta:
            model_metadata = state_delta[METADATA_KEY_MODEL]
            if isinstance(model_metadata, dict):
                return model_metadata.get("node_id")

        # Fallback to legacy flat structure
        return state_delta.get("node_id")

    @staticmethod
    def get_duration_ms(event: Event) -> float:
        """Get the duration in milliseconds from a graph event.

        Extracts from structured metadata if available, otherwise from flat state_delta.
        """
        if not event.actions or not event.actions.state_delta:
            return 0.0

        state_delta = event.actions.state_delta

        # Try structured metadata first
        for metadata_key in [METADATA_KEY_NODE, METADATA_KEY_TOOL, METADATA_KEY_MODEL]:
            if metadata_key in state_delta:
                metadata = state_delta[metadata_key]
                if isinstance(metadata, dict):
                    duration = metadata.get("duration_ms")
                    if duration is not None:
                        return float(duration)

        # Fallback to legacy flat structure
        return state_delta.get("duration_ms", 0.0)

    @classmethod
    def get_metadata(cls, event: Event, key: str, metadata_cls: Type[T]) -> Optional[T]:
        """Generic method to extract typed metadata from event.

        Args:
            event: Event to extract metadata from
            key: Metadata key constant
            metadata_cls: Metadata dataclass type

        Returns:
            Metadata instance if found, None otherwise
        """
        if not event.actions or not event.actions.state_delta:
            return None

        metadata_dict = event.actions.state_delta.get(key)
        if metadata_dict and isinstance(metadata_dict, dict):
            try:
                return metadata_cls(**metadata_dict)
            except (TypeError, ValueError):
                return None
        return None

    @classmethod
    def get_node_metadata(cls, event: Event) -> Optional[NodeExecutionMetadata]:
        """Extract NodeExecutionMetadata from an event.

        Args:
            event: Event to extract metadata from

        Returns:
            NodeExecutionMetadata if found, None otherwise
        """
        return cls.get_metadata(event, METADATA_KEY_NODE, NodeExecutionMetadata)

    @classmethod
    def get_tool_metadata(cls, event: Event) -> Optional[ToolExecutionMetadata]:
        """Extract ToolExecutionMetadata from an event.

        Args:
            event: Event to extract metadata from

        Returns:
            ToolExecutionMetadata if found, None otherwise
        """
        return cls.get_metadata(event, METADATA_KEY_TOOL, ToolExecutionMetadata)

    @classmethod
    def get_model_metadata(cls, event: Event) -> Optional[ModelExecutionMetadata]:
        """Extract ModelExecutionMetadata from an event.

        Args:
            event: Event to extract metadata from

        Returns:
            ModelExecutionMetadata if found, None otherwise
        """
        return cls.get_metadata(event, METADATA_KEY_MODEL, ModelExecutionMetadata)

    # Note: cache/pregel/checkpoint helpers are intentionally omitted.
