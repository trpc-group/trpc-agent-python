# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""trpc-claw memory service."""

from datetime import datetime
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.memory import MemoryServiceConfig
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import MemoryEntry
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import SearchMemoryResponse

from ..storage import HISTORY_KEY
from ..storage import LONG_TERM_MEMORY_KEY
from ..storage import StorageManager
from ..storage import get_memory_key


class ClawMemoryService(InMemoryMemoryService):
    """trpc-claw memory service."""

    def __init__(self,
                 storage_manager: StorageManager,
                 memory_service_config: Optional[MemoryServiceConfig] = None,
                 enabled: bool = False):
        """Initialize the claw memory service.

        Args:
            storage_manager: The storage manager.
            memory_service_config: The memory service config.
            enabled: Whether the memory service is enabled.
        """
        super().__init__(memory_service_config=memory_service_config, enabled=enabled)
        self.storage_manager = storage_manager

    @override
    async def store_session(self, session: Session, agent_context: Optional[AgentContext] = None) -> None:
        """Store session in file storage."""
        await super().store_session(session, agent_context)
        if not agent_context:
            raise ValueError("Agent context is required")
        long_term_memory: Event = agent_context.get_metadata(LONG_TERM_MEMORY_KEY, "")
        history_entry: str = agent_context.get_metadata(HISTORY_KEY, "")
        if long_term_memory:
            await self.storage_manager.write_long_term(get_memory_key(session), long_term_memory.content.parts[0].text)
        if history_entry:
            await self.storage_manager.append_history(get_memory_key(session), str(history_entry))

    @override
    async def search_memory(self,
                            key: str,
                            query: str,
                            limit: int = 10,
                            agent_context: Optional[AgentContext] = None) -> SearchMemoryResponse:
        """Search memory in file storage."""
        response = SearchMemoryResponse()
        long_term_memory = await self.storage_manager.read_long_term(key)
        if long_term_memory:
            response.memories.extend([
                MemoryEntry(content=Content(parts=[Part.from_text(text=long_term_memory)]),
                            author="system",
                            timestamp=datetime.now().isoformat())
            ])
        return response
