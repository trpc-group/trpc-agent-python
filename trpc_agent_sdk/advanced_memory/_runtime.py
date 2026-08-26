# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unified runtime entry point for the independent memory mechanism."""

from __future__ import annotations

from dataclasses import dataclass

from ._config import AdvancedMemoryConfig
from ._coordination import SessionOperationCoordinator
from ._paths import AdvancedMemoryPaths
from ._storage import LongTermMemoryStore
from ._storage import SessionMemoryStore
from ._storage import ToolResultStore
from ._storage import TranscriptStore


@dataclass(frozen=True)
class AdvancedMemoryRuntime:
    """Aggregate configuration, paths, and the three storage objects."""

    config: AdvancedMemoryConfig
    paths: AdvancedMemoryPaths
    coordination: SessionOperationCoordinator
    long_term_memory: LongTermMemoryStore
    session_memory: SessionMemoryStore
    tool_results: ToolResultStore
    transcripts: TranscriptStore

    @classmethod
    def create(cls, config: AdvancedMemoryConfig | None = None) -> "AdvancedMemoryRuntime":
        """Create a runtime isolated from the legacy mechanism."""
        resolved_config = config or AdvancedMemoryConfig()
        paths = AdvancedMemoryPaths(resolved_config)
        return cls(
            config=resolved_config,
            paths=paths,
            coordination=SessionOperationCoordinator(),
            long_term_memory=LongTermMemoryStore(resolved_config, paths),
            session_memory=SessionMemoryStore(resolved_config, paths),
            tool_results=ToolResultStore(resolved_config, paths),
            transcripts=TranscriptStore(resolved_config, paths),
        )

    async def initialize(self) -> bool:
        """Create memory directories only when the mechanism is enabled."""
        if not self.config.enabled:
            return False
        await self.long_term_memory.initialize()
        return True
