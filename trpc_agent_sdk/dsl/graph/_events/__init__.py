# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Events submodule for graph execution events.

This module consolidates all event-related functionality:
- Constants and enums
- Metadata dataclasses
- EventBuilder for creating events
- EventUtils for extracting event information
"""

# Constants and enums
from ._builder import EventBuilder
from ._constants import ExecutionPhase
from ._constants import GraphEventType
from ._constants import METADATA_KEY_MODEL
from ._constants import METADATA_KEY_NODE
from ._constants import METADATA_KEY_STATE
from ._constants import METADATA_KEY_TOOL
from ._helpers import EventUtils
from ._metadata import CompletionMetadata
from ._metadata import ModelExecutionMetadata
from ._metadata import NodeExecutionMetadata
from ._metadata import StateUpdateMetadata
from ._metadata import ToolExecutionMetadata

__all__ = [
    "EventBuilder",
    "ExecutionPhase",
    "GraphEventType",
    "METADATA_KEY_MODEL",
    "METADATA_KEY_NODE",
    "METADATA_KEY_STATE",
    "METADATA_KEY_TOOL",
    "EventUtils",
    "CompletionMetadata",
    "ModelExecutionMetadata",
    "NodeExecutionMetadata",
    "StateUpdateMetadata",
    "ToolExecutionMetadata",
]
