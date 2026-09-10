# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Runner-compatible facade for the Advanced Memory mechanism."""

from __future__ import annotations

from typing import Any
from typing import Optional
from typing import TYPE_CHECKING

from typing_extensions import override

from trpc_agent_sdk.abc import MemoryServiceABC
from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.abc import SearchMemoryResponse
from trpc_agent_sdk.abc import SessionABC
from trpc_agent_sdk.abc import SessionServiceABC
from trpc_agent_sdk.context import AgentContext

if TYPE_CHECKING:
    from trpc_agent_sdk.memory.advanced_memory import AdvancedMemoryServiceConfig
    from trpc_agent_sdk.memory.advanced_memory import AdvancedMemoryRuntime
    from trpc_agent_sdk.memory.advanced_memory import LongTermMemoryIntegration


class AdvancedMemoryService(MemoryServiceABC):
    """Expose tool-driven long-term Memory through the Runner memory API.

    ``Runner`` calls :meth:`bind` automatically. The standard
    :class:`MemoryServiceABC` methods are implemented for lifecycle
    compatibility; long-term memory is intentionally still written and read
    by the Agent through the Advanced Memory tools. Session compression is
    configured independently through ``SessionService.session_compact_manager``.
    """

    def __init__(
        self,
        config: AdvancedMemoryServiceConfig | None = None,
        *,
        runtime: AdvancedMemoryRuntime | None = None,
        preload_memory_model: Any | None = None,
        install_long_term_memory_tools: bool = True,
    ) -> None:
        """Create an Advanced Memory service without binding it to an agent."""
        from trpc_agent_sdk.memory.advanced_memory import AdvancedMemoryServiceConfig
        from trpc_agent_sdk.memory.advanced_memory import AdvancedMemoryRuntime

        if config is not None and runtime is not None and config != runtime.config:
            raise ValueError("config and runtime must describe the same Advanced Memory configuration")
        resolved_config = runtime.config if runtime is not None else (config or AdvancedMemoryServiceConfig())
        super().__init__(MemoryServiceConfig(enabled=resolved_config.enabled))
        self._runtime = runtime or AdvancedMemoryRuntime.create(resolved_config)
        self._preload_memory_model = preload_memory_model
        self._install_long_term_memory_tools = install_long_term_memory_tools
        self._integration: LongTermMemoryIntegration | None = None
        self._bound_agent: Any | None = None

    @property
    def config(self) -> AdvancedMemoryServiceConfig:
        """Return the Advanced Memory configuration."""
        return self._runtime.config

    @property
    def runtime(self) -> AdvancedMemoryRuntime:
        """Return the Advanced Memory runtime."""
        return self._runtime

    @property
    def integration(self) -> LongTermMemoryIntegration | None:
        """Return the binding result after the service is attached to a Runner."""
        return self._integration

    def bind(self, agent: Any, session_service: SessionServiceABC) -> SessionServiceABC:
        """Bind long-term Memory and return the unchanged SessionService."""
        from trpc_agent_sdk.memory.advanced_memory import setup_long_term_memory

        if self._integration is not None:
            if agent is not self._bound_agent:
                raise ValueError("AdvancedMemoryService is already bound to another agent")
            return session_service

        self._integration = setup_long_term_memory(
            agent,
            self._runtime,
            preload_memory_model=self._preload_memory_model,
            install_tools=self._install_long_term_memory_tools,
        )
        self._bound_agent = agent
        return session_service

    @override
    async def store_session(
        self,
        session: SessionABC,
        agent_context: Optional[AgentContext] = None,
    ) -> None:
        """Keep the standard hook side-effect free.

        Advanced Memory is model-directed: the Agent decides what is durable
        and calls ``save_memory``. Automatically storing every Session here
        would mix transient conversation history with long-term memory.
        """
        return None

    @override
    async def search_memory(
        self,
        key: str,
        query: str,
        limit: int = 10,
        agent_context: Optional[AgentContext] = None,
    ) -> SearchMemoryResponse:
        """Return the standard empty response for compatibility.

        Advanced long-term memory is intentionally accessed through its
        ``save_memory``, ``read_memory``, and ``list_memory_index`` tools.
        """
        return SearchMemoryResponse()

    @override
    async def close(self) -> None:
        """Release service-owned local or external storage resources."""
        await self._runtime.close()
