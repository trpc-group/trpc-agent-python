# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Optional long-term memory APIs."""

from ._config import AdvancedMemoryServiceConfig
from trpc_agent_sdk.sessions.compact._formats import MemoryDocument
from trpc_agent_sdk.sessions.compact._formats import MemoryIndexEntry
from trpc_agent_sdk.sessions.compact._formats import MemoryType
from trpc_agent_sdk.sessions.compact._formats import memory_freshness
from trpc_agent_sdk.sessions.compact._formats import parse_memory_updated_at
from ._paths import AdvancedMemoryPaths
from ._paths import MemoryScope
from ._runtime import AdvancedMemoryRuntime
from ._runtime import ScopedAdvancedMemoryRuntime
from ._storage import LongTermMemoryStore

from ._integration import LongTermMemoryIntegration
from ._integration import setup_long_term_memory
from ._memory_context import LongTermMemoryContext
from ._memory_context import LongTermMemoryContextCallback
from ._memory_context import setup_long_term_memory_context
from ._preload_memory import MemoryCandidate
from ._preload_memory import MemoryPreloader
from ._preload_memory import MemoryRelevanceSelector
from ._preload_memory import ModelMemoryRelevanceSelector
from ._preload_memory import select_relevant_memory_filenames
from ._storage_backend import AdvancedMemoryStorageBackend
from ._storage_backend import LocalAdvancedMemoryStorageBackend

__all__ = [
    "AdvancedMemoryStorageBackend",
    "AdvancedMemoryServiceConfig",
    "LongTermMemoryIntegration",
    "AdvancedMemoryPaths",
    "AdvancedMemoryRuntime",
    "ScopedAdvancedMemoryRuntime",
    "LongTermMemoryStore",
    "LocalAdvancedMemoryStorageBackend",
    "LongTermMemoryContext",
    "LongTermMemoryContextCallback",
    "MemoryDocument",
    "MemoryScope",
    "MemoryIndexEntry",
    "MemoryType",
    "MemoryCandidate",
    "MemoryPreloader",
    "MemoryRelevanceSelector",
    "ModelMemoryRelevanceSelector",
    "select_relevant_memory_filenames",
    "memory_freshness",
    "parse_memory_updated_at",
    "setup_long_term_memory_context",
    "setup_long_term_memory",
]
