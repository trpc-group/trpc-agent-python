"""Unified runtime entry point for the independent memory mechanism."""

from __future__ import annotations

from dataclasses import dataclass

from .config import AdvancedMemoryConfig
from .coordination import SessionOperationCoordinator
from .paths import AdvancedMemoryPaths
from .storage import LongTermMemoryStore
from .storage import SessionMemoryStore
from .storage import ToolResultStore
from .storage import TranscriptStore


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
